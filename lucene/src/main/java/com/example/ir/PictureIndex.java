package com.example.ir;

import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.Filters;
import com.mongodb.client.model.Indexes;
import com.mongodb.client.model.Projections;
import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.FloatDocValuesField;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.index.StoredFields;
import org.apache.lucene.index.Term;
import org.apache.lucene.queries.function.FunctionScoreQuery;
import org.apache.lucene.queryparser.classic.MultiFieldQueryParser;
import org.apache.lucene.queryparser.classic.ParseException;
import org.apache.lucene.queryparser.classic.QueryParser;
import org.apache.lucene.search.BooleanClause;
import org.apache.lucene.search.BooleanQuery;
import org.apache.lucene.search.DoubleValuesSource;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.MatchNoDocsQuery;
import org.apache.lucene.search.Query;
import org.apache.lucene.search.ScoreDoc;
import org.apache.lucene.search.TermQuery;
import org.apache.lucene.search.TopDocs;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;
import org.bson.conversions.Bson;

import java.io.IOException;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Tim anh bang van ban di kem anh (crawler khong luu alt / chu thich anh):
 *
 *   name    : ten tep trong URL, "le-tot-nghiep-2024.jpg" -> "le tot nghiep 2024"     (x1.5)
 *   title   : tieu de cac trang chua anh (html.images), toi da {@value #MAX_TITLES}    (x2)
 *   context : {@value #CONTEXT_CHARS} ky tu dau noi dung cac trang do                  (x1)
 *
 * Bo anh trang tri: anh nam tren hon {@value #MAX_PAGES} trang (logo, banner, icon chung cua site), ten co
 * logo/icon/avatar..., svg / ico, va anh da tai nho hon {@value #MIN_BYTES} byte. Anh nam tren it trang dac
 * trung hon: diem BM25 nhan 1 / (1 + ln so trang).
 *
 * Chi muc rieng trong thu muc con {@value #SUBDIR}/ cua chi muc chinh (cung volume khi chay Docker), co moc
 * dong bo rieng: moi luot lay anh cua cac trang html va cac ban ghi picture co updated_at >= moc.
 */
final class PictureIndex {

    static final String SUBDIR = "pictures";
    static final int MAX_PAGES = 20;
    private static final int MAX_TITLES = 5;
    private static final int CONTEXT_CHARS = 300;
    private static final int MIN_BYTES = 3000;
    /** Toi da so anh cung 1 trang trong 1 trang ket qua, de ket qua khong bi 1 album chiem het. */
    private static final int MAX_PER_PAGE = 4;
    private static final int CHUNK = 200;
    /** URL dai hon thi bo (vd. anh nhung data:/base64 bi ghi thanh URL), Lucene gioi han 1 term 32 KB. */
    private static final int MAX_URL = 2000;

    static final String[] FIELDS = {"title", "name", "context"};
    static final Map<String, Float> BOOSTS = Map.of("title", 2.0f, "name", 1.5f, "context", 1.0f);
    private static final String SPEC = "spec";
    private static final String SIG = "sig";

    private static final Pattern NOISE = Pattern.compile(
            "logo|icon|favicon|avatar|gravatar|sprite|banner|loading|spacer|blank|placeholder|no_image|noimage|"
            + "button|arrow|flag|social|facebook|zalo|youtube|twitter|qrcode|qr-code");
    private static final Set<String> SKIP_FORMATS = Set.of("svg", "ico");

    /** 1 trang chua anh: url, tieu de, dau noi dung. */
    record Page(String url, String title, String context) {}

    /** Thong tin tai anh trong collection picture. */
    record Info(long size, String format) {
        boolean fetched() {
            return size > 0;
        }
    }

    record Hit(String url, String page, String title, String name, int pages, float score) {}

    record Result(String query, String error, String parsed, long total, List<Hit> hits) {}

    private PictureIndex() {}

    static Path dir(Path indexDir) {
        return indexDir.resolve(SUBDIR);
    }

    static boolean exists(Path indexDir) throws IOException {
        Path d = dir(indexDir);
        if (!Files.isDirectory(d)) return false;
        try (Directory dir = FSDirectory.open(d)) {
            return DirectoryReader.indexExists(dir);
        }
    }

    // ---- lap chi muc ----

    /** Index tren html.images (tim trang chua 1 anh) va index phu cua picture (doc ma khong cham toi b64). */
    static void ensureMongoIndexes(MongoDatabase db) {
        db.getCollection("html").createIndex(Indexes.ascending("images"));
        db.getCollection("picture").createIndex(Indexes.ascending("size", "url", "format"));
    }

    /** Lap lai toan bo chi muc anh tu MongoDB. @return so anh trong chi muc */
    static int build(Path indexDir, MongoDatabase db, Analyzer analyzer, String since) throws IOException {
        ensureMongoIndexes(db);
        long t0 = System.nanoTime();
        List<Page> pages = new ArrayList<>();
        Map<String, List<Integer>> byImage = new LinkedHashMap<>();
        for (org.bson.Document d : db.getCollection("html")
                .find(Filters.and(Filters.exists("images.0"), Filters.exists("duplicate_of", false)))
                .projection(pageProjection()).batchSize(2000)) {
            int idx = pages.size();
            pages.add(page(d));
            for (String img : images(d)) byImage.computeIfAbsent(img, k -> new ArrayList<>()).add(idx);
        }
        Map<String, Info> infos = new HashMap<>();
        // covered query tren index (size, url, format): khong doc b64
        for (org.bson.Document d : db.getCollection("picture").find(Filters.gt("size", 0))
                .projection(Projections.fields(Projections.include("size", "url", "format"), Projections.excludeId()))
                .hint(Indexes.ascending("size", "url", "format")).batchSize(10_000)) {
            infos.put(d.getString("url"), info(d));
        }
        System.out.printf("  Anh: %,d trang co anh, %,d anh khac nhau, %,d anh da tai (%.1f s)%n",
                pages.size(), byImage.size(), infos.size(), (System.nanoTime() - t0) / 1e9);

        Files.createDirectories(dir(indexDir));
        try (Directory dir = FSDirectory.open(dir(indexDir));
             IndexWriter writer = new IndexWriter(dir, config(analyzer, IndexWriterConfig.OpenMode.CREATE))) {
            int n = 0;
            for (Map.Entry<String, List<Integer>> e : byImage.entrySet()) {
                List<Page> ps = e.getValue().stream().map(pages::get).toList();
                Document doc = toLuceneDoc(e.getKey(), ps, infos.get(e.getKey()));
                if (doc == null) continue;
                try {
                    writer.addDocument(doc);
                } catch (IllegalArgumentException ex) {
                    System.err.println("  [bo qua] " + e.getKey() + ": " + ex.getMessage());
                    continue;
                }
                n++;
            }
            if (since != null) writer.setLiveCommitData(Map.of(Indexer.SINCE, since).entrySet());
            writer.commit();
            System.out.printf("  Da lap chi muc %,d anh -> %s (%.1f s)%n", n, dir(indexDir).toAbsolutePath(),
                    (System.nanoTime() - t0) / 1e9);
            return n;
        }
    }

    /**
     * Dong bo tang dan: anh cua cac trang html va cac ban ghi picture co updated_at >= moc cua chi muc anh.
     * Moi anh bi anh huong duoc dung lai tu dau (so trang chua no, tieu de, trang thai tai).
     */
    static Indexer.SyncStats sync(Path indexDir, MongoDatabase db, Analyzer analyzer, String newSince)
            throws IOException {
        String since = since(indexDir);
        if (since == null) return new Indexer.SyncStats(0, 0, 0, 0);
        Set<String> affected = new LinkedHashSet<>();
        for (org.bson.Document d : db.getCollection("html").find(Filters.gte("updated_at", since))
                .projection(Projections.fields(Projections.include("images"), Projections.excludeId()))) {
            affected.addAll(images(d));
        }
        for (org.bson.Document d : db.getCollection("picture").find(Filters.gte("updated_at", since))
                .projection(Projections.fields(Projections.include("url"), Projections.excludeId()))) {
            if (d.getString("url") != null) affected.add(d.getString("url"));
        }
        int added = 0, updated = 0, deleted = 0, unchanged = 0;
        try (Directory dir = FSDirectory.open(dir(indexDir));
             IndexWriter writer = new IndexWriter(dir, config(analyzer, IndexWriterConfig.OpenMode.APPEND));
             DirectoryReader reader = DirectoryReader.open(writer)) {
            IndexSearcher searcher = new IndexSearcher(reader);
            StoredFields stored = searcher.storedFields();
            List<String> all = new ArrayList<>(affected);
            for (int i = 0; i < all.size(); i += CHUNK) {
                List<String> chunk = all.subList(i, Math.min(all.size(), i + CHUNK));
                Map<String, List<Page>> pagesOf = new HashMap<>();
                for (org.bson.Document d : db.getCollection("html")
                        .find(Filters.and(Filters.in("images", chunk), Filters.exists("duplicate_of", false)))
                        .projection(pageProjection())) {
                    Page p = page(d);
                    for (String img : images(d)) {
                        if (chunk.contains(img)) pagesOf.computeIfAbsent(img, k -> new ArrayList<>()).add(p);
                    }
                }
                Map<String, Info> infos = new HashMap<>();
                for (org.bson.Document d : db.getCollection("picture").find(Filters.in("url", chunk))
                        .projection(Projections.fields(Projections.include("size", "url", "format"),
                                Projections.excludeId()))) {
                    infos.put(d.getString("url"), info(d));
                }
                for (String url : chunk) {
                    Term id = new Term("id", url);
                    TopDocs td = searcher.search(new TermQuery(id), 1);
                    boolean exists = td.scoreDocs.length > 0;
                    Document doc = toLuceneDoc(url, pagesOf.getOrDefault(url, List.of()), infos.get(url));
                    if (doc == null) {
                        if (exists) {
                            writer.deleteDocuments(id);
                            deleted++;
                        }
                        continue;
                    }
                    if (exists && doc.get(SIG).equals(stored.document(td.scoreDocs[0].doc, Set.of(SIG)).get(SIG))) {
                        unchanged++;
                        continue;
                    }
                    try {
                        writer.updateDocument(id, doc);
                    } catch (IllegalArgumentException ex) {
                        System.err.println("  [bo qua] " + url + ": " + ex.getMessage());
                        continue;
                    }
                    if (exists) updated++;
                    else added++;
                }
            }
            writer.setLiveCommitData(Map.of(Indexer.SINCE, newSince).entrySet());
            writer.commit();
        }
        return new Indexer.SyncStats(added, updated, deleted, unchanged);
    }

    static String since(Path indexDir) throws IOException {
        if (!exists(indexDir)) return null;
        try (Directory dir = FSDirectory.open(dir(indexDir));
             DirectoryReader r = DirectoryReader.open(dir)) {
            return r.getIndexCommit().getUserData().get(Indexer.SINCE);
        }
    }

    private static IndexWriterConfig config(Analyzer analyzer, IndexWriterConfig.OpenMode mode) {
        IndexWriterConfig cfg = new IndexWriterConfig(analyzer);
        cfg.setOpenMode(mode);
        cfg.setRAMBufferSizeMB(128);
        return cfg;
    }

    private static Bson pageProjection() {
        return Projections.fields(
                Projections.include("url", "title", "images"),
                Projections.computed("ctx", new org.bson.Document("$substrCP",
                        List.of(new org.bson.Document("$ifNull", List.of("$content", "")), 0, CONTEXT_CHARS))),
                Projections.excludeId());
    }

    private static Page page(org.bson.Document d) {
        return new Page(d.getString("url"), nullToEmpty(d.getString("title")).replaceAll("\\s+", " ").strip(),
                nullToEmpty(d.getString("ctx")));
    }

    private static List<String> images(org.bson.Document d) {
        List<String> out = new ArrayList<>();
        Object v = d.get("images");
        if (v instanceof List<?> l) {
            for (Object o : l) if (o instanceof String s && !s.isBlank()) out.add(s);
        }
        return out;
    }

    private static Info info(org.bson.Document d) {
        Number size = d.get("size", Number.class);
        return new Info(size == null ? 0 : size.longValue(), nullToEmpty(d.getString("format")));
    }

    /** null neu anh khong dua vao chi muc (anh trang tri, khong con trang nao chua, ...). */
    static Document toLuceneDoc(String url, List<Page> pages, Info info) {
        if (pages.isEmpty() || pages.size() > MAX_PAGES || url.length() > MAX_URL
                || !url.regionMatches(true, 0, "http", 0, 4)) return null;
        String path = path(url).toLowerCase(Locale.ROOT);
        if (NOISE.matcher(path).find()) return null;
        String format = info != null && !info.format().isBlank() ? info.format().toLowerCase(Locale.ROOT)
                : extension(path);
        if (SKIP_FORMATS.contains(format)) return null;
        if (info != null && info.fetched() && info.size() < MIN_BYTES) return null;

        Set<String> titles = new LinkedHashSet<>();
        StringBuilder context = new StringBuilder();
        for (Page p : pages) {
            if (!p.title().isBlank() && titles.size() < MAX_TITLES) titles.add(p.title());
            if (context.length() < 3 * CONTEXT_CHARS && !p.context().isBlank()) context.append(p.context()).append('\n');
        }
        String name = nameWords(url);
        String title = String.join(" | ", titles);

        Document doc = new Document();
        doc.add(new StringField("id", url, Field.Store.YES));
        doc.add(new StringField("fetched", info != null && info.fetched() ? "1" : "0", Field.Store.NO));
        doc.add(new TextField("name", name, Field.Store.YES));
        doc.add(new TextField("title", title, Field.Store.YES));
        doc.add(new TextField("context", context.toString(), Field.Store.NO));
        doc.add(new StoredField("page", pages.get(0).url()));
        doc.add(new StoredField("pages", pages.size()));
        doc.add(new FloatDocValuesField(SPEC, (float) (1 / (1 + Math.log(pages.size())))));
        doc.add(new StoredField(SIG, Integer.toHexString((name + '\0' + title + '\0' + context + '\0'
                + pages.size() + '\0' + (info != null && info.fetched())).hashCode())));
        return doc;
    }

    /** "https://x/a/le-tot-nghiep_2024-150x150.jpg" -> "le tot nghiep 2024" (bo so do / chuoi hex dai). */
    static String nameWords(String url) {
        String last = path(url);
        last = last.substring(last.lastIndexOf('/') + 1);
        last = URLDecoder.decode(last.replace("+", "%2B"), StandardCharsets.UTF_8);
        last = last.replaceFirst("\\.[A-Za-z0-9]{2,5}$", "").replaceAll("-\\d+x\\d+$", "");
        List<String> words = new ArrayList<>();
        for (String w : last.split("[^\\p{L}\\p{N}]+")) {
            if (w.isEmpty() || w.matches("(?i)[0-9a-f]{6,}") || w.matches("\\d{5,}")) continue;
            words.add(w);
        }
        return String.join(" ", words);
    }

    private static String path(String url) {
        try {
            String p = URI.create(url.replace(" ", "%20")).getRawPath();
            return p == null ? "" : p;
        } catch (IllegalArgumentException e) {
            return url;
        }
    }

    private static String extension(String path) {
        int dot = path.lastIndexOf('.');
        return dot < 0 || dot < path.lastIndexOf('/') ? "" : path.substring(dot + 1);
    }

    private static String nullToEmpty(String s) {
        return s == null ? "" : s;
    }

    // ---- tim kiem ----

    /** Tim anh; all = false thi chi anh da tai (hien duoc). */
    static Result search(IndexSearcher searcher, Analyzer analyzer, String text, int limit, boolean all)
            throws IOException {
        String raw = text.strip();
        boolean plain = !Searcher.LUCENE_SYNTAX.matcher(raw).find();
        Query q;
        if (analyzer instanceof VietnameseAnalyzer && plain && raw.contains(" ")) {
            q = Searcher.buildVietnamesePhraseQuery(analyzer, raw, FIELDS, BOOSTS);
        } else {
            QueryParser parser = new MultiFieldQueryParser(FIELDS, analyzer, BOOSTS);
            try {
                q = parser.parse(raw);
            } catch (ParseException e) {
                try {
                    q = parser.parse(QueryParser.escape(raw));
                } catch (ParseException e2) {
                    return new Result(raw, "khong phan tich duoc truy van: " + e2.getMessage(), "", 0, List.of());
                }
            }
        }
        if (q instanceof MatchNoDocsQuery || q.toString().isBlank()) {
            return new Result(raw, "khong con tu khoa nao sau khi loc tu dung / dau cau", "", 0, List.of());
        }
        String parsed = q.toString();
        BooleanQuery.Builder b = new BooleanQuery.Builder().add(q, BooleanClause.Occur.MUST);
        if (!all) b.add(new TermQuery(new Term("fetched", "1")), BooleanClause.Occur.FILTER);
        Query scoring = FunctionScoreQuery.boostByValue(b.build(), DoubleValuesSource.fromFloatField(SPEC));

        TopDocs top = searcher.search(scoring, limit * 3 + 20);
        StoredFields stored = searcher.storedFields();
        Map<String, Integer> perPage = new HashMap<>();
        Set<String> seen = new HashSet<>();
        List<Hit> hits = new ArrayList<>();
        for (ScoreDoc sd : top.scoreDocs) {
            if (hits.size() == limit) break;
            Document d = stored.document(sd.doc);
            String url = d.get("id");
            String page = d.get("page");
            // cung 1 anh nhieu ban trong 1 trang (abc-150x150.jpg, abc.jpg, /mobile/abc.jpg) va toi da
            // MAX_PER_PAGE anh moi trang
            String file = url.substring(url.lastIndexOf('/') + 1).replaceAll("-\\d+x\\d+(\\.[A-Za-z0-9]+)$", "$1");
            if (!seen.add(page + "\n" + file)) continue;
            if (perPage.merge(page, 1, Integer::sum) > MAX_PER_PAGE) continue;
            String title = d.get("title");
            int cut = title.indexOf(" | ");
            hits.add(new Hit(url, page, cut < 0 ? title : title.substring(0, cut), d.get("name"),
                    d.getField("pages").numericValue().intValue(), sd.score));
        }
        return new Result(raw, null, parsed, top.totalHits.value, hits);
    }
}
