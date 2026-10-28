package com.example.ir;

import com.mongodb.ConnectionString;
import com.mongodb.MongoClientSettings;
import com.mongodb.MongoException;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoCursor;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.Filters;
import com.mongodb.client.model.Indexes;
import com.mongodb.client.model.Projections;
import org.bson.Document;
import org.bson.conversions.Bson;

import java.io.IOException;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Iterator;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.concurrent.TimeUnit;

/**
 * Doc corpus cua crawler tu MongoDB (database information_retrieval) -> document cho Indexer.
 *
 *   html  : 1 trang = 1 tai lieu (url, title, content, intro); bo trang trung noi dung (co duplicate_of)
 *   other : 1 tep PDF/DOCX/... = 1 tai lieu (url, format, content); title lay tu ten tep trong URL;
 *           intro tinh tu graph (crawler chi tinh intro cho html) - xem {@link #introFor(String)}
 *   graph : khong lap chi muc rieng - anchor text cua trang da duoc crawler gop vao html.intro
 *
 * Doc kieu stream (cursor), khong nap het vao RAM. Schema: crawler/docs/data-schema.md#mongodb.
 *
 * Dong bo tang dan ({@link #changes(String)}): crawler them trang moi, thu thap lai trang cu, tinh lai
 * intro cua trang duoc tro toi - moi lan ghi deu dat updated_at (ISO 8601 UTC, so sanh duoc nhu chuoi).
 * Doc cac document co updated_at >= moc; trang trung noi dung / rong tra ve de XOA khoi chi muc.
 * Them: cac tep ma nhung trang vua doi tro toi (intro cua tep doi theo lien ket, updated_at cua tep thi khong).
 */
final class MongoDocLoader implements Iterable<DocLoader.Doc>, AutoCloseable {

    static final String DEFAULT_URI = "mongodb://localhost:27018";
    static final String DEFAULT_DB = "information_retrieval";

    private static final List<String> COLLECTIONS = List.of("html", "other");
    /** Nhu crawler (domain/storage/intro.py): giu toi da 20 van ban lien ket. */
    private static final int MAX_INTRO_TEXTS = 20;
    /** So url trang moi lan hoi graph khi tim tep bi anh huong. */
    private static final int LINK_CHUNK = 500;
    // Lo nho de moi lan getMore khong cach nhau qua lau khi tach tu cham (cursor het han sau 10 phut).
    private static final int BATCH_SIZE = 100;

    private final MongoClient client;
    private final MongoDatabase db;
    private final long limit;

    private MongoDocLoader(MongoClient client, MongoDatabase db, long limit) {
        this.client = client;
        this.db = db;
        this.limit = limit;
    }

    /** Ket noi va kiem tra MongoDB; limit = 0 la lay het. */
    static MongoDocLoader open(String uri, String dbName, long limit) throws IOException {
        MongoClientSettings settings = MongoClientSettings.builder()
                .applyConnectionString(new ConnectionString(uri))
                .applyToClusterSettings(b -> b.serverSelectionTimeout(5, TimeUnit.SECONDS))
                .build();
        MongoClient client = MongoClients.create(settings);
        try {
            MongoDatabase db = client.getDatabase(dbName);
            db.runCommand(new Document("ping", 1));
            if (!db.listCollectionNames().into(new java.util.ArrayList<>()).contains("html")) {
                throw new IOException("database '" + dbName + "' chua co collection html."
                        + "\n  Nap ban dump:  docker compose run --rm restore");
            }
            return new MongoDocLoader(client, db, limit);
        } catch (MongoException e) {
            client.close();
            throw new IOException("khong ket noi duoc MongoDB tai " + uri + ": " + e.getMessage()
                    + "\n  Bat MongoDB:  docker compose up -d mongo", e);
        } catch (IOException e) {
            client.close();
            throw e;
        }
    }

    /** So document (uoc luong) cua tung collection, de in tien do. */
    String describe() {
        StringBuilder sb = new StringBuilder();
        for (String c : COLLECTIONS) {
            if (!sb.isEmpty()) sb.append(", ");
            sb.append(c).append(" ~").append(String.format("%,d", db.getCollection(c).estimatedDocumentCount()));
        }
        return sb.toString();
    }

    MongoDatabase database() {
        return db;
    }

    /** 1 thay doi can dua vao chi muc: doc = null nghia la xoa url khoi chi muc. */
    record Change(String url, DocLoader.Doc doc) {}

    /** Thoi diem hien tai theo dinh dang updated_at cua crawler: 2026-10-18T13:01:17+00:00. */
    static String timestamp(Instant t) {
        return TIMESTAMP.format(t);
    }

    private static final DateTimeFormatter TIMESTAMP =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'+00:00'").withZone(ZoneOffset.UTC);

    /** Index tren updated_at de moi luot dong bo khong phai quet ca collection (tao 1 lan, lan sau bo qua). */
    void ensureUpdatedAtIndex() {
        for (String c : COLLECTIONS) {
            db.getCollection(c).createIndex(Indexes.ascending("updated_at"));
        }
    }

    /**
     * Cac document html / other co updated_at >= since (them moi, cap nhat, hoac thanh trung / rong),
     * roi cac tep (other) ma nhung trang html vua doi tro toi - vi van ban lien ket toi tep co the vua doi.
     */
    Iterable<Change> changes(String since) {
        return () -> new Iterator<>() {
            private int phase;                                   // 0 html, 1 other, 2 tep duoc tro toi
            private final java.util.Set<String> changedPages = new java.util.HashSet<>();
            private final java.util.Set<String> seenFiles = new java.util.HashSet<>();
            private MongoCursor<Document> cursor;

            @Override
            public boolean hasNext() {
                while (cursor == null || !cursor.hasNext()) {
                    if (cursor != null) cursor.close();
                    cursor = null;
                    if (phase == 3) return false;
                    cursor = switch (phase++) {
                        case 0 -> changedSince("html", since);
                        case 1 -> changedSince("other", since);
                        default -> linkedFiles(changedPages, seenFiles);
                    };
                    if (cursor == null) return false;
                }
                return true;
            }

            @Override
            public Change next() {
                if (!hasNext()) throw new NoSuchElementException();
                Document d = cursor.next();
                String collection = phase == 1 ? "html" : "other";
                String url = d.getString("url");
                if (phase == 1) changedPages.add(url);
                else seenFiles.add(url);
                return new Change(url, toDoc(collection, d));
            }
        };
    }

    private MongoCursor<Document> changedSince(String collection, String since) {
        return db.getCollection(collection)
                .find(Filters.gte("updated_at", since))
                .projection(Projections.fields(
                        Projections.include("url", "title", "intro", "content", "format", "duplicate_of"),
                        Projections.excludeId()))
                .batchSize(BATCH_SIZE)
                .noCursorTimeout(true)
                .iterator();
    }

    /** Cac tep (other) duoc nhung trang trong pages tro toi, tru cac tep da xu ly o luot nay. */
    private MongoCursor<Document> linkedFiles(java.util.Set<String> pages, java.util.Set<String> seen) {
        java.util.Set<String> dests = new java.util.HashSet<>();
        List<String> all = new java.util.ArrayList<>(pages);
        for (int i = 0; i < all.size(); i += LINK_CHUNK) {
            List<String> chunk = all.subList(i, Math.min(all.size(), i + LINK_CHUNK));
            for (Document e : db.getCollection("graph").find(Filters.in("src", chunk))
                    .projection(Projections.fields(Projections.include("dest"), Projections.excludeId()))) {
                dests.add(e.getString("dest"));
            }
        }
        dests.removeAll(seen);
        dests.removeAll(pages);
        if (dests.isEmpty()) return null;
        return db.getCollection("other")
                .find(Filters.in("url", dests))
                .projection(Projections.fields(
                        Projections.include("url", "title", "content", "format"), Projections.excludeId()))
                .batchSize(BATCH_SIZE)
                .noCursorTimeout(true)
                .iterator();
    }

    /**
     * Van ban lien ket tro toi url, tinh nhu crawler tinh html.intro (build_intro): gop khoang trang,
     * bo rong, bo trung, sap theo so lan xuat hien giam dan (bang nhau thi theo chu), toi da 20 dong.
     */
    String introFor(String url) {
        java.util.Map<String, Integer> counts = new java.util.HashMap<>();
        for (Document e : db.getCollection("graph").find(Filters.eq("dest", url))
                .projection(Projections.fields(Projections.include("intro"), Projections.excludeId()))) {
            String t = e.getString("intro");
            if (t == null) continue;
            t = t.strip().replaceAll("\\s+", " ");
            if (!t.isEmpty()) counts.merge(t, 1, Integer::sum);
        }
        return counts.entrySet().stream()
                .sorted(java.util.Map.Entry.<String, Integer>comparingByValue().reversed()
                        .thenComparing(java.util.Map.Entry.comparingByKey()))
                .limit(MAX_INTRO_TEXTS)
                .map(java.util.Map.Entry::getKey)
                .collect(java.util.stream.Collectors.joining("\n"));
    }

    @Override
    public Iterator<DocLoader.Doc> iterator() {
        return new Docs();
    }

    @Override
    public void close() {
        client.close();
    }

    private MongoCursor<Document> openCursor(String collection) {
        Bson filter = collection.equals("html")
                ? Filters.and(Filters.exists("duplicate_of", false), Filters.ne("content", ""))
                : Filters.ne("content", "");
        return db.getCollection(collection)
                .find(filter)
                .projection(Projections.fields(
                        Projections.include("url", "title", "intro", "content", "format"),
                        Projections.excludeId()))
                .batchSize(BATCH_SIZE)
                .noCursorTimeout(true)
                .iterator();
    }

    /** null neu document khong duoc lap chi muc: rong, hoac trang trung noi dung voi trang khac. */
    private DocLoader.Doc toDoc(String collection, Document d) {
        String url = d.getString("url");
        String content = d.getString("content");
        if (url == null || content == null || content.isBlank() || d.containsKey("duplicate_of")) {
            return null;
        }
        String title = nullToEmpty(d.getString("title"));
        if (collection.equals("html")) {
            return new DocLoader.Doc(url, "mongo:html", title, nullToEmpty(d.getString("intro")), content);
        }
        if (title.isBlank()) {
            title = fileName(url);
        }
        return new DocLoader.Doc(url, "mongo:other (" + nullToEmpty(d.getString("format")) + ")", title,
                introFor(url), content);
    }

    /** "https://x/a/Quy%20che.pdf" -> "Quy che.pdf" (de tim duoc tep theo ten). */
    private static String fileName(String url) {
        try {
            String path = URI.create(url).getRawPath();
            if (path == null) return "";
            String last = path.substring(path.lastIndexOf('/') + 1);
            return URLDecoder.decode(last, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            return "";
        }
    }

    private static String nullToEmpty(String s) {
        return s == null ? "" : s;
    }

    /** Duyet lan luot html roi other; mo cursor sau chi khi cursor truoc het. */
    private final class Docs implements Iterator<DocLoader.Doc> {
        private final Iterator<String> collections = COLLECTIONS.iterator();
        private String collection;
        private MongoCursor<Document> cursor;
        private DocLoader.Doc next;
        private long served;

        @Override
        public boolean hasNext() {
            while (next == null) {
                if (limit > 0 && served >= limit) {
                    closeCursor();
                    return false;
                }
                if (cursor != null && cursor.hasNext()) {
                    next = toDoc(collection, cursor.next());
                    continue;
                }
                closeCursor();
                if (!collections.hasNext()) {
                    return false;
                }
                collection = collections.next();
                cursor = openCursor(collection);
            }
            return true;
        }

        @Override
        public DocLoader.Doc next() {
            if (!hasNext()) {
                throw new NoSuchElementException();
            }
            DocLoader.Doc d = next;
            next = null;
            served++;
            return d;
        }

        private void closeCursor() {
            if (cursor != null) {
                cursor.close();
                cursor = null;
            }
        }
    }
}
