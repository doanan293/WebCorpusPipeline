package com.example.ir;

import com.mongodb.ConnectionString;
import com.mongodb.MongoClientSettings;
import com.mongodb.MongoException;
import com.mongodb.client.FindIterable;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.Filters;
import com.mongodb.client.model.Indexes;
import com.mongodb.client.model.Projections;
import com.mongodb.client.model.Sorts;
import com.sun.net.httpserver.HttpExchange;
import org.bson.Document;
import org.bson.conversions.Bson;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Pattern;

/**
 * Doc truc tiep du lieu crawler trong MongoDB cho man hinh "Du lieu" cua frontend.
 *
 *   GET /api/stats                                                    : so document moi loai
 *   GET /api/docs?kind=html|other|picture&page=&size=&since=&host=&fetched=1 : danh sach, moi nhat truoc
 *       (fetched=1: chi anh da tai, vi ban ghi anh moi chua co byte; tong so la so dem o nen)
 *   GET /api/doc?kind=...&url=...                                     : chi tiet mot ban ghi
 *   GET /api/picture?url=...                                          : byte cua anh
 */
final class DataApi {

    private static final int MAX_SIZE = 100;
    private static final int MAX_CONTENT = 5000;
    private static final int MAX_OUTLINKS = 100;
    private static final long FETCHED_TTL_MS = TimeUnit.MINUTES.toMillis(5);

    private static final Map<String, List<String>> ROW_FIELDS = Map.of(
            "html", List.of("url", "title", "updated_at", "duplicate_of"),
            "other", List.of("url", "format", "origin", "updated_at"),
            "picture", List.of("url", "origin", "format", "size", "error", "updated_at"));

    private static final Map<String, List<String>> DETAIL_FIELDS = Map.of(
            "html", List.of("url", "title", "content", "intro", "images", "updated_at", "duplicate_of"),
            "other", List.of("url", "format", "origin", "content", "updated_at"),
            "picture", List.of("url", "origin", "format", "size", "error", "updated_at"));

    private final MongoDatabase db;
    private final AtomicBoolean counting = new AtomicBoolean();
    private volatile Long pictureFetched;
    private volatile long pictureFetchedAt;

    private DataApi(MongoDatabase db) {
        this.db = db;
    }

    static DataApi open(String mongoUri, String mongoDb) {
        MongoClient client = MongoClients.create(MongoClientSettings.builder()
                .applyConnectionString(new ConnectionString(mongoUri))
                .applyToClusterSettings(b -> b.serverSelectionTimeout(5, TimeUnit.SECONDS))
                .build());
        MongoDatabase db = client.getDatabase(mongoDb);
        for (String c : ROW_FIELDS.keySet()) {
            db.getCollection(c).createIndex(Indexes.ascending("updated_at"));
        }
        return new DataApi(db);
    }

    /** Tra loi neu path thuoc API nay; false de WebServer xu ly tiep. */
    boolean handle(HttpExchange ex, String path, Map<String, String> q) throws IOException {
        if (!List.of("/api/stats", "/api/docs", "/api/doc", "/api/picture").contains(path)) return false;
        try {
            switch (path) {
                case "/api/stats" -> json(ex, 200, stats());
                case "/api/docs" -> docs(ex, q);
                case "/api/doc" -> doc(ex, q);
                default -> picture(ex, q);
            }
        } catch (MongoException e) {
            e.printStackTrace();
            json(ex, 503, new Document("error", "khong doc duoc MongoDB"));
        }
        return true;
    }

    private Document stats() {
        refreshPictureFetched();
        return new Document("html", db.getCollection("html").estimatedDocumentCount())
                .append("other", db.getCollection("other").estimatedDocumentCount())
                .append("picture", db.getCollection("picture").estimatedDocumentCount())
                .append("pictureFetched", pictureFetched);
    }

    /** Dem anh da tai phai quet ca collection nen dem o luong nen; chua dem xong thi tra null. */
    private void refreshPictureFetched() {
        long now = System.currentTimeMillis();
        if (now - pictureFetchedAt < FETCHED_TTL_MS || !counting.compareAndSet(false, true)) return;
        Thread t = new Thread(() -> {
            try {
                pictureFetched = db.getCollection("picture")
                        .countDocuments(Filters.and(Filters.exists("b64"), Filters.ne("b64", "")));
                pictureFetchedAt = System.currentTimeMillis();
            } catch (MongoException e) {
                e.printStackTrace();
            } finally {
                counting.set(false);
            }
        }, "picture-count");
        t.setDaemon(true);
        t.start();
    }

    private void docs(HttpExchange ex, Map<String, String> q) throws IOException {
        String kind = q.getOrDefault("kind", "html");
        if (!ROW_FIELDS.containsKey(kind)) {
            json(ex, 400, new Document("error", "kind phai la html, other hoac picture"));
            return;
        }
        int page = Math.max(1, intParam(q, "page", 1));
        int size = Math.max(1, Math.min(MAX_SIZE, intParam(q, "size", 20)));
        List<Bson> filters = new ArrayList<>();
        String since = q.get("since");
        if (since != null && !since.isBlank()) filters.add(Filters.gte("updated_at", since));
        String host = q.get("host");
        if (host != null && !host.isBlank()) {
            filters.add(Filters.regex("url", "^https?://" + Pattern.quote(host) + "(:\\d+)?(/|$)"));
        }
        boolean fetchedOnly = kind.equals("picture") && "1".equals(q.get("fetched"));
        if (fetchedOnly) refreshPictureFetched();
        MongoCollection<Document> c = db.getCollection(kind);
        long total;
        FindIterable<Document> found;
        if (fetchedOnly && filters.isEmpty()) {
            Long counted = pictureFetched;
            total = counted != null ? counted : c.estimatedDocumentCount();
            found = c.find(Filters.ne("size", null));
        } else if (filters.isEmpty()) {
            total = c.estimatedDocumentCount();
            found = c.find();
        } else {
            Bson filter = Filters.and(filters);
            total = c.countDocuments(filter);
            found = c.find(filter);
        }
        List<Document> rows = found.sort(Sorts.descending("updated_at"))
                .skip((page - 1) * size)
                .limit(size)
                .projection(Projections.fields(Projections.include(ROW_FIELDS.get(kind)), Projections.excludeId()))
                .into(new ArrayList<>());
        json(ex, 200, new Document("total", total).append("page", page).append("size", size).append("docs", rows));
    }

    private void doc(HttpExchange ex, Map<String, String> q) throws IOException {
        String kind = q.getOrDefault("kind", "html");
        String url = q.get("url");
        if (!DETAIL_FIELDS.containsKey(kind) || url == null) {
            json(ex, 400, new Document("error", "can kind va url"));
            return;
        }
        Document d = db.getCollection(kind).find(Filters.eq("url", url))
                .projection(Projections.fields(Projections.include(DETAIL_FIELDS.get(kind)), Projections.excludeId()))
                .first();
        if (d == null) {
            json(ex, 404, new Document("error", "khong co ban ghi"));
            return;
        }
        String content = d.getString("content");
        if (content != null && content.length() > MAX_CONTENT) d.put("content", content.substring(0, MAX_CONTENT));
        if (kind.equals("html")) {
            List<String> out = new ArrayList<>();
            for (Document e : db.getCollection("graph").find(Filters.eq("src", url))
                    .projection(Projections.include("dest")).limit(MAX_OUTLINKS)) {
                out.add(e.getString("dest"));
            }
            d.put("outlinks", out);
        }
        json(ex, 200, d);
    }

    private void picture(HttpExchange ex, Map<String, String> q) throws IOException {
        String url = q.get("url");
        Document d = url == null ? null : db.getCollection("picture").find(Filters.eq("url", url))
                .projection(Projections.include("b64", "format")).first();
        String b64 = d == null ? null : d.getString("b64");
        byte[] body;
        try {
            body = b64 == null || b64.isEmpty() ? null : Base64.getDecoder().decode(b64);
        } catch (IllegalArgumentException e) {
            body = null;
        }
        if (body == null) {
            json(ex, 404, new Document("error", "anh chua tai"));
            return;
        }
        // Anh lay tu web, co the la SVG co script: khong cho chay gi cung origin voi trang.
        ex.getResponseHeaders().set("Content-Type", imageType(d.getString("format")));
        ex.getResponseHeaders().set("Content-Security-Policy", "sandbox");
        ex.getResponseHeaders().set("X-Content-Type-Options", "nosniff");
        ex.getResponseHeaders().set("Cache-Control", "max-age=86400");
        ex.sendResponseHeaders(200, body.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(body);
        }
    }

    private static String imageType(String format) {
        return switch (format == null ? "" : format.toLowerCase(Locale.ROOT)) {
            case "jpg", "jpeg" -> "image/jpeg";
            case "png" -> "image/png";
            case "gif" -> "image/gif";
            case "webp" -> "image/webp";
            case "svg" -> "image/svg+xml";
            default -> "application/octet-stream";
        };
    }

    private static int intParam(Map<String, String> q, String name, int fallback) {
        try {
            return Integer.parseInt(q.getOrDefault(name, String.valueOf(fallback)));
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    private static void json(HttpExchange ex, int status, Document body) throws IOException {
        WebServer.send(ex, status, "application/json", body.toJson().getBytes(StandardCharsets.UTF_8));
    }
}
