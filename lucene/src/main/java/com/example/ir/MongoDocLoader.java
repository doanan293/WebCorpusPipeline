package com.example.ir;

import com.mongodb.ConnectionString;
import com.mongodb.MongoClientSettings;
import com.mongodb.MongoException;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoCursor;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.Filters;
import com.mongodb.client.model.Projections;
import org.bson.Document;
import org.bson.conversions.Bson;

import java.io.IOException;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.concurrent.TimeUnit;

/**
 * Doc corpus cua crawler tu MongoDB (database information_retrieval) -> document cho Indexer.
 *
 *   html  : 1 trang = 1 tai lieu (url, title, content, intro); bo trang trung noi dung (co duplicate_of)
 *   other : 1 tep PDF/DOCX/... = 1 tai lieu (url, format, content); title lay tu ten tep trong URL
 *   graph : khong lap chi muc rieng - anchor text da duoc crawler gop vao html.intro
 *
 * Doc kieu stream (cursor), khong nap het vao RAM. Schema: crawler/docs/data-schema.md#mongodb.
 */
final class MongoDocLoader implements Iterable<DocLoader.Doc>, AutoCloseable {

    static final String DEFAULT_URI = "mongodb://localhost:27018";
    static final String DEFAULT_DB = "information_retrieval";

    private static final List<String> COLLECTIONS = List.of("html", "other");
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

    private static DocLoader.Doc toDoc(String collection, Document d) {
        String url = d.getString("url");
        String content = d.getString("content");
        if (url == null || content == null || content.isBlank()) {
            return null;
        }
        String title = nullToEmpty(d.getString("title"));
        if (collection.equals("html")) {
            return new DocLoader.Doc(url, "mongo:html", title, nullToEmpty(d.getString("intro")), content);
        }
        if (title.isBlank()) {
            title = fileName(url);
        }
        return new DocLoader.Doc(url, "mongo:other (" + nullToEmpty(d.getString("format")) + ")", title, "", content);
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
