package com.example.ir;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

/**
 * Giao dien web don gian cho tim kiem (HttpServer co san trong JDK, khong them thu vien).
 *
 *   GET /                          : trang tim kiem (src/main/resources/web/index.html)
 *   GET /api/search?q=...&limit=10 : ket qua dang JSON
 *   GET /api/info                  : so van ban trong chi muc
 *
 * Truoc moi truy van mo lai chi muc neu --mode sync vua ghi (openIfChanged), nen du lieu moi hien
 * ngay tren trang. Cac truy van chay noi tiep nhau (bo tach tu VnCoreNLP dung chung 1 the hien).
 */
final class WebServer {

    private static final int MAX_LIMIT = 50;

    private final Directory dir;
    private final Analyzer analyzer;
    private final Synonyms synonyms;
    private final double dedup;
    private final float pagerankWeight;
    private final byte[] page;
    private DirectoryReader reader;

    private WebServer(Path indexDir, Analyzer analyzer, Synonyms synonyms, double dedup, float pagerankWeight)
            throws IOException {
        this.dir = FSDirectory.open(indexDir);
        this.reader = DirectoryReader.open(dir);
        this.analyzer = analyzer;
        this.synonyms = synonyms;
        this.dedup = dedup;
        this.pagerankWeight = pagerankWeight;
        try (InputStream in = WebServer.class.getResourceAsStream("/web/index.html")) {
            if (in == null) throw new IOException("thieu tai nguyen /web/index.html");
            this.page = in.readAllBytes();
        }
    }

    static void run(Path indexDir, Analyzer analyzer, Synonyms synonyms, double dedup, float pagerankWeight,
                    int port) throws IOException {
        WebServer app = new WebServer(indexDir, analyzer, synonyms, dedup, pagerankWeight);
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/", app::handle);
        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        System.out.println("Chi muc: " + app.reader.numDocs() + " van ban");
        System.out.println("Mo trinh duyet: http://localhost:" + port + "/   (Ctrl+C de dung)");
    }

    private void handle(HttpExchange ex) throws IOException {
        try (ex) {
            String path = ex.getRequestURI().getPath();
            if (!ex.getRequestMethod().equals("GET")) {
                send(ex, 405, "text/plain", "method not allowed".getBytes(StandardCharsets.UTF_8));
            } else if (path.equals("/") || path.equals("/index.html")) {
                send(ex, 200, "text/html", page);
            } else if (path.equals("/api/search")) {
                Map<String, String> q = query(ex.getRequestURI().getRawQuery());
                String text = q.getOrDefault("q", "").strip();
                int limit = 10;
                try {
                    limit = Math.max(1, Math.min(MAX_LIMIT, Integer.parseInt(q.getOrDefault("limit", "10"))));
                } catch (NumberFormatException ignored) {
                    // giu mac dinh
                }
                String body = text.isEmpty() ? "{\"error\":\"truy van rong\"}" : search(text, limit);
                send(ex, 200, "application/json", body.getBytes(StandardCharsets.UTF_8));
            } else if (path.equals("/api/info")) {
                int n;
                synchronized (this) {
                    refresh();
                    n = reader.numDocs();
                }
                send(ex, 200, "application/json", ("{\"docs\":" + n + "}").getBytes(StandardCharsets.UTF_8));
            } else {
                send(ex, 404, "text/plain", "not found".getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private synchronized String search(String text, int limit) throws IOException {
        refresh();
        long t0 = System.nanoTime();
        Searcher.Outcome o = Searcher.search(Searcher.Ctx.open(reader, analyzer, synonyms, dedup, pagerankWeight),
                text, limit);
        double ms = (System.nanoTime() - t0) / 1e6;
        System.out.printf("[web] %s -> %d ket qua (%.0f ms)%n", text, o.total(), ms);
        return toJson(o, ms, reader.numDocs());
    }

    /** Mo lai chi muc neu --mode sync / --mode pagerank vua commit. */
    private void refresh() throws IOException {
        DirectoryReader newer = DirectoryReader.openIfChanged(reader);
        if (newer != null) {
            reader.close();
            reader = newer;
        }
    }

    private static String toJson(Searcher.Outcome o, double ms, int docs) {
        StringBuilder sb = new StringBuilder("{");
        field(sb, "query", o.query()).append(',');
        if (o.error() != null) field(sb, "error", "khong phan tich duoc truy van: " + o.error()).append(',');
        if (o.noTerms()) field(sb, "error", "khong con tu khoa nao sau khi loc tu dung / dau cau").append(',');
        sb.append("\"autoPhrase\":").append(o.autoPhrase()).append(',');
        sb.append("\"expansions\":[");
        for (int i = 0; i < o.expansions().size(); i++) {
            if (i > 0) sb.append(',');
            str(sb, o.expansions().get(i));
        }
        sb.append("],");
        field(sb, "parsed", o.parsed()).append(',');
        sb.append("\"pagerank\":").append(o.usePagerank()).append(',');
        sb.append("\"total\":").append(o.total()).append(',');
        sb.append("\"hidden\":").append(o.hidden()).append(',');
        sb.append("\"docs\":").append(docs).append(',');
        sb.append("\"ms\":").append(Math.round(ms)).append(',');
        if (o.suggestion() != null) {
            sb.append("\"suggestion\":{");
            field(sb, "text", o.suggestion().text()).append(',');
            sb.append("\"hits\":").append(o.suggestion().hits()).append(',');
            sb.append("\"originalHits\":").append(o.suggestion().originalHits()).append("},");
        }
        sb.append("\"hits\":");
        hits(sb, o.hits());
        sb.append(",\"extra\":");
        hits(sb, o.extra());
        return sb.append('}').toString();
    }

    private static void hits(StringBuilder sb, List<Searcher.Hit> hits) {
        sb.append('[');
        for (int i = 0; i < hits.size(); i++) {
            Searcher.Hit h = hits.get(i);
            if (i > 0) sb.append(',');
            sb.append('{');
            field(sb, "id", h.id()).append(',');
            field(sb, "title", h.title()).append(',');
            field(sb, "source", h.source()).append(',');
            sb.append("\"score\":").append(round(h.score())).append(',');
            sb.append("\"tfidf\":").append(round(h.tfidf())).append(',');
            sb.append("\"pagerank\":").append(round(h.pagerank())).append(',');
            // doan trich: escape HTML truoc, roi moi doi dau danh dau thanh <mark>
            String snippet = html(h.snippet() == null ? "" : h.snippet())
                    .replace(Searcher.markOpen(), "<mark>").replace(Searcher.markClose(), "</mark>");
            field(sb, "snippet", snippet);
            sb.append('}');
        }
        sb.append(']');
    }

    private static double round(double v) {
        return Math.round(v * 10000) / 10000.0;
    }

    private static StringBuilder field(StringBuilder sb, String name, String value) {
        str(sb, name);
        sb.append(':');
        return str(sb, value);
    }

    private static StringBuilder str(StringBuilder sb, String s) {
        if (s == null) return sb.append("null");
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20 || c == ' ' || c == ' ') sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
                }
            }
        }
        return sb.append('"');
    }

    private static String html(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    private static Map<String, String> query(String raw) {
        Map<String, String> m = new HashMap<>();
        if (raw == null) return m;
        for (String kv : raw.split("&")) {
            int eq = kv.indexOf('=');
            String k = eq < 0 ? kv : kv.substring(0, eq);
            String v = eq < 0 ? "" : kv.substring(eq + 1);
            m.put(URLDecoder.decode(k, StandardCharsets.UTF_8), URLDecoder.decode(v, StandardCharsets.UTF_8));
        }
        return m;
    }

    private static void send(HttpExchange ex, int status, String type, byte[] body) throws IOException {
        ex.getResponseHeaders().set("Content-Type", type + "; charset=utf-8");
        ex.sendResponseHeaders(status, body.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(body);
        }
    }
}
