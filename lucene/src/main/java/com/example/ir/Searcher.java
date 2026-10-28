package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.TokenStream;
import org.apache.lucene.analysis.tokenattributes.CharTermAttribute;
import org.apache.lucene.document.Document;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexReader;
import org.apache.lucene.index.StoredFields;
import org.apache.lucene.index.Term;
import org.apache.lucene.queryparser.classic.MultiFieldQueryParser;
import org.apache.lucene.queryparser.classic.ParseException;
import org.apache.lucene.queryparser.classic.QueryParser;
import org.apache.lucene.search.BooleanClause;
import org.apache.lucene.search.BooleanQuery;
import org.apache.lucene.search.BoostQuery;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.MatchNoDocsQuery;
import org.apache.lucene.search.PhraseQuery;
import org.apache.lucene.search.Query;
import org.apache.lucene.search.ScoreDoc;
import org.apache.lucene.search.TermQuery;
import org.apache.lucene.search.TopDocs;
import org.apache.lucene.search.similarities.ClassicSimilarity;
import org.apache.lucene.search.highlight.Highlighter;
import org.apache.lucene.search.highlight.InvalidTokenOffsetsException;
import org.apache.lucene.search.highlight.QueryScorer;
import org.apache.lucene.search.highlight.SimpleHTMLFormatter;
import org.apache.lucene.search.highlight.SimpleSpanFragmenter;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;

/** Chay truy van tren chi muc theo 2 kieu: batch (tu file) va interactive (nhap tay). */
public final class Searcher {

    static final String DEFAULT_FIELD = "content";

    /**
     * Cac field duoc tim va trong so: khop tieu de / anchor text (intro) dang tin hon khop noi dung.
     * Chi muc lap tu thu muc khong co intro -> clause intro khong khop gi, khong anh huong.
     */
    static final Map<String, Float> FIELD_BOOSTS = Map.of("title", 3.0f, "intro", 1.5f, "content", 1.0f);
    private static final String[] FIELDS = {"title", "intro", "content"};

    // Cac dau hieu cu phap QueryParser: co bat ky cai nao -> KHONG tu dong coi la cum.
    private static final java.util.regex.Pattern LUCENE_SYNTAX = java.util.regex.Pattern.compile(
            "[\"()\\[\\]{}:^~*?\\\\]|\\bAND\\b|\\bOR\\b|\\bNOT\\b");

    private static final int FRAGMENT_CHARS = 100;   // do rong doan trich quanh tu khoa
    private static final int MAX_FRAGMENTS = 3;      // so doan trich toi da moi ket qua
    private static final int FALLBACK_CHARS = 200;   // khi khong lam noi bat duoc

    // Highlighter boc tu khop bang 2 ky tu danh dau noi bo, sau do teeSink dich ra:
    //  - console (neu la terminal that): to nen vang, chu den (ma ANSI)
    //  - file .txt (va console khi bi chuyen huong): boc bang « ... »
    private static final String MARK_A = "";
    private static final String MARK_B = "";
    private static final String ANSI_ON = "[30;43m";
    private static final String ANSI_OFF = "[0m";
    private static final String TEXT_ON = "«";
    private static final String TEXT_OFF = "»";
    private static final boolean COLOR = colorEnabled();

    /** Console co ho tro ANSI khong? Tren Windows chi bat neu la Windows Terminal / ConEmu / VS Code. */
    private static boolean colorEnabled() {
        if (System.getenv("NO_COLOR") != null) {
            return false;
        }
        if (System.console() == null) {
            return false; // bi pipe/redirect
        }
        String os = System.getProperty("os.name", "").toLowerCase(Locale.ROOT);
        if (os.contains("win")) {
            return System.getenv("WT_SESSION") != null       // Windows Terminal
                    || System.getenv("ConEmuANSI") != null    // ConEmu / Cmder
                    || "1".equals(System.getenv("ANSICON"))
                    || System.getenv("TERM_PROGRAM") != null;  // VS Code terminal
        }
        return true;
    }

    private Searcher() {}

    /** Kieu 1: doc toan bo truy van tu danh sach (nap tu file), in console + ghi file. */
    public static void runBatch(Path indexDir, List<QueryLoader.Query> queries, Analyzer analyzer,
                                Synonyms synonyms, double dedup, int topK, Path outFile) throws IOException {

        Files.createDirectories(outFile.toAbsolutePath().getParent());

        try (Directory dir = FSDirectory.open(indexDir);
             DirectoryReader reader = DirectoryReader.open(dir);
             BufferedWriter out = Files.newBufferedWriter(outFile, StandardCharsets.UTF_8)) {

            IndexSearcher searcher = new IndexSearcher(reader);
            IndexSearcher tfidfSearcher = new IndexSearcher(reader);
            tfidfSearcher.setSimilarity(new ClassicSimilarity());
            StoredFields storedFields = searcher.storedFields();
            QueryParser parser = newParser(analyzer);
            Extras extras = new Extras(synonyms, spellSuggester(reader), dedup);
            Consumer<String> sink = teeSink(out);

            sink.accept("# Ket qua tim kiem Lucene (che do batch)");
            sink.accept("# So van ban trong chi muc: " + reader.numDocs());
            sink.accept("# So truy van: " + queries.size() + " | top-K = " + topK);
            sink.accept("");

            for (QueryLoader.Query q : queries) {
                executeAndFormat(searcher, tfidfSearcher, reader, storedFields, analyzer, parser, extras, q, topK, sink);
            }
        }
        System.out.println();
        System.out.println("Da ghi ket qua chi tiet vao: " + outFile.toAbsolutePath());
    }

    /**
     * Kieu 2: nhap lan luot tung truy van tu console, hien ket qua ngay sau moi truy van.
     * Truoc moi truy van mo lai chi muc neu co commit moi (vd. --mode sync dang chay o tien trinh
     * khac) -> thay ngay van ban vua them / cap nhat ma khong can khoi dong lai.
     */
    public static void runInteractive(Path indexDir, Analyzer analyzer, Synonyms synonyms, double dedup,
                                      int topK, Path outFile) throws IOException {

        Files.createDirectories(outFile.toAbsolutePath().getParent());

        try (Directory dir = FSDirectory.open(indexDir);
             BufferedWriter out = Files.newBufferedWriter(outFile, StandardCharsets.UTF_8);
             BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {

            DirectoryReader reader = DirectoryReader.open(dir);
            try {
                QueryParser parser = newParser(analyzer);
                Consumer<String> sink = teeSink(out);

                sink.accept("# Ket qua tim kiem Lucene (che do interactive)");
                sink.accept("# So van ban trong chi muc: " + reader.numDocs() + " | top-K = " + topK);
                sink.accept("");
                System.out.println("Nhap truy van roi Enter. Go ':q' (hoac Ctrl+D) de thoat.");
                if (analyzer instanceof VietnameseAnalyzer) {
                    System.out.println("(cum tieng Viet cu go binh thuong, khong can ngoac kep)");
                }

                int n = 0;
                while (true) {
                    System.out.print("\ntruy van> ");
                    System.out.flush();
                    String line = in.readLine();
                    if (line == null) break;                       // Ctrl+D / het input
                    line = line.strip();
                    if (line.isEmpty()) continue;
                    if (line.equals(":q") || line.equals(":quit") || line.equals("exit")) break;

                    DirectoryReader newer = DirectoryReader.openIfChanged(reader);
                    if (newer != null) {
                        reader.close();
                        reader = newer;
                        sink.accept("# (chi muc vua duoc cap nhat: " + reader.numDocs() + " van ban)");
                    }
                    IndexSearcher searcher = new IndexSearcher(reader);
                    IndexSearcher tfidfSearcher = new IndexSearcher(reader);
                    tfidfSearcher.setSimilarity(new ClassicSimilarity());
                    Extras extras = new Extras(synonyms, spellSuggester(reader), dedup);

                    QueryLoader.Query q = new QueryLoader.Query("Q" + (++n), line);
                    executeAndFormat(searcher, tfidfSearcher, reader, searcher.storedFields(), analyzer, parser,
                            extras, q, topK, sink);
                }
                sink.accept("");
                sink.accept("# Ket thuc phien: " + n + " truy van.");
            } finally {
                reader.close();
            }
        }
        System.out.println("\nDa ghi lai phien vao: " + outFile.toAbsolutePath());
    }

    // ---- phan dung chung ----

    /** Goi y chinh ta khi truy van co it ket qua hon nguong nay. */
    private static final int SPELL_BELOW = 20;
    /** Trong so cua cum mo rong (viet tat / dong nghia) so voi truy van goc. */
    private static final float SYNONYM_BOOST = 0.5f;

    /**
     * Cac bo sung quanh truy van: mo rong viet tat / dong nghia, goi y chinh ta (null neu chi muc
     * chua co field spell), nguong Jaccard de an ket qua gan trung (0 = tat).
     */
    private record Extras(Synonyms synonyms, SpellSuggester spell, double dedup) {}

    private static SpellSuggester spellSuggester(IndexReader reader) throws IOException {
        SpellSuggester s = new SpellSuggester(reader);
        return s.available() ? s : null;
    }

    private static QueryParser newParser(Analyzer analyzer) {
        // Tu khong ghi field -> tim tren ca title/intro/content (kem trong so); "title:..." van dung duoc
        QueryParser parser = new MultiFieldQueryParser(FIELDS, analyzer, FIELD_BOOSTS);
        parser.setDefaultOperator(QueryParser.Operator.OR);
        return parser;
    }

    /** Do "xe dich" cho phep giua cac am tiet trong PhraseQuery (chiu duoc 1 dao vi tri). */
    private static final int VN_SLOP = 2;

    /**
     * Truy van cho 1 cum tieng Viet "tron":
     *   PhraseQuery tren CHUOI AM TIET (slop {@value VN_SLOP})^2   SHOULD  - clause chinh, chinh xac
     *   + moi TU GHEP la 1 TermQuery ^0.4                          SHOULD  - phu, giup xep hang
     *
     * Vi am tiet phai nam GAN NHAU dung thu tu nen "dai hoc" chi khop noi co "dai"
     * ngay truoc "hoc", KHONG khop "sinh hoc" / "khoa hoc". Neu tap van ban that su
     * khong co cum do -> 0 ket qua (dung), khong co clause AND keo dai lung tung.
     * Nhieu khai niem roi thi go co toan tu: Lucene AND "tieng Viet".
     */
    private static Query buildVietnamesePhraseQuery(Analyzer analyzer, String text) throws IOException {
        List<String> words = analyzeToTokens(analyzer, text);
        if (words.isEmpty()) {
            return new MatchNoDocsQuery("khong con tu khoa");
        }

        List<String> sylls = new ArrayList<>();
        for (String w : words) {
            if (w.indexOf('_') > 0) {
                for (String s : w.split("_")) {
                    if (!s.isBlank()) {
                        sylls.add(s);
                    }
                }
            } else {
                sylls.add(w);
            }
        }
        return acrossFields(field -> {
            if (sylls.size() == 1) {
                return new TermQuery(new Term(field, sylls.get(0)));
            }
            PhraseQuery.Builder pb = new PhraseQuery.Builder();
            pb.setSlop(VN_SLOP);
            for (String s : sylls) {
                pb.add(new Term(field, s));
            }

            BooleanQuery.Builder bb = new BooleanQuery.Builder();
            bb.add(new BoostQuery(pb.build(), 2.0f), BooleanClause.Occur.SHOULD);
            for (String w : words) {
                if (w.indexOf('_') > 0) { // chi tu ghep (du dac trung) moi lam clause phu
                    bb.add(new BoostQuery(new TermQuery(new Term(field, w)), 0.4f),
                            BooleanClause.Occur.SHOULD);
                }
            }
            return bb.build();
        });
    }

    /** Dung cung 1 truy van tren moi field trong FIELDS, nhan trong so cua field, ghep bang OR. */
    private static Query acrossFields(java.util.function.Function<String, Query> perField) {
        BooleanQuery.Builder bb = new BooleanQuery.Builder();
        for (String field : FIELDS) {
            bb.add(new BoostQuery(perField.apply(field), FIELD_BOOSTS.get(field)), BooleanClause.Occur.SHOULD);
        }
        return bb.build();
    }

    /**
     * Truy van "thuan" cho tf-idf: OR tung TU duoc tach ra tren moi field (khong PhraseQuery, khong boost),
     * dung cong thuc mac dinh cua Lucene (tf x idf x fieldNorm cho tung tu, cong don).
     */
    private static Query buildPlainQuery(Analyzer analyzer, String text) throws IOException {
        List<String> words = analyzeToTokens(analyzer, text);
        if (words.isEmpty()) {
            return new MatchNoDocsQuery("khong con tu khoa");
        }
        java.util.LinkedHashSet<String> unique = new java.util.LinkedHashSet<>(words);
        BooleanQuery.Builder bb = new BooleanQuery.Builder();
        for (String field : FIELDS) {
            for (String w : unique) {
                bb.add(new TermQuery(new Term(field, w)), BooleanClause.Occur.SHOULD);
            }
        }
        return bb.build();
    }

    /** Tach text bang analyzer, chi lay TU (bo qua am tiet type=SYLLABLE va dau cau). */
    private static List<String> analyzeToTokens(Analyzer analyzer, String text) throws IOException {
        List<String> out = new ArrayList<>();
        try (TokenStream ts = analyzer.tokenStream(DEFAULT_FIELD, text)) {
            CharTermAttribute term = ts.addAttribute(CharTermAttribute.class);
            org.apache.lucene.analysis.tokenattributes.TypeAttribute type =
                    ts.addAttribute(org.apache.lucene.analysis.tokenattributes.TypeAttribute.class);
            ts.reset();
            while (ts.incrementToken()) {
                if (CompoundSplitFilter.SYLLABLE_TYPE.equals(type.type())) {
                    continue; // am tiet tach ra tu tu ghep
                }
                String tok = term.toString();
                if (tok.chars().anyMatch(Character::isLetterOrDigit)) {
                    out.add(tok);
                }
            }
            ts.end();
        }
        return out;
    }

    /** Consumer ghi dong thoi ra console (co the co mau) va ra file (« ... »). */
    private static Consumer<String> teeSink(BufferedWriter out) {
        return line -> {
            String forConsole = COLOR
                    ? line.replace(MARK_A, ANSI_ON).replace(MARK_B, ANSI_OFF)
                    : line.replace(MARK_A, TEXT_ON).replace(MARK_B, TEXT_OFF);
            System.out.println(forConsole);
            try {
                out.write(line.replace(MARK_A, TEXT_ON).replace(MARK_B, TEXT_OFF));
                out.newLine();
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        };
    }

    /** Parse + tim kiem 1 truy van, xuat ket qua kem doan trich chua tu khoa. */
    private static void executeAndFormat(IndexSearcher searcher, IndexSearcher tfidfSearcher, IndexReader reader,
                                         StoredFields storedFields, Analyzer analyzer, QueryParser parser,
                                         Extras extras, QueryLoader.Query q, int topK,
                                         Consumer<String> sink) throws IOException {
        sink.accept("==================================================");
        sink.accept("Truy van " + q.id() + ": " + q.text());

        // Voi analyzer tieng Viet: truy van "tron" (nhieu tu, khong co toan tu) duoc dung
        // thanh (cum dung thu tu)^2  HOAC  tung tu rieng le. Nho clause "tung tu" nen van
        // ra ket qua khi bo tach tu chia cum hoi khac giua truy van va tai lieu.
        // Muon dieu khien tay thi dung AND/OR/NOT/ngoac kep nhu binh thuong.
        String raw = q.text().strip();
        boolean plain = !LUCENE_SYNTAX.matcher(raw).find();
        boolean autoPhrase = analyzer instanceof VietnameseAnalyzer
                && raw.contains(" ")
                && plain;

        Query luceneQuery;
        if (autoPhrase) {
            luceneQuery = buildVietnamesePhraseQuery(analyzer, raw);
            sink.accept("  (tu hieu la cum tieng Viet; them AND/OR/\"\" de tuy chinh)");
        } else {
            try {
                luceneQuery = parser.parse(q.text());
            } catch (ParseException e) {
                try {
                    luceneQuery = parser.parse(QueryParser.escape(q.text()));
                } catch (ParseException e2) {
                    sink.accept("  [loi] khong phan tich duoc truy van: " + e2.getMessage());
                    sink.accept("");
                    return;
                }
            }
        }

        String parsed = luceneQuery.toString();
        if (parsed.isBlank() || luceneQuery instanceof MatchNoDocsQuery) {
            sink.accept("  (khong con tu khoa nao sau khi loc tu dung / dau cau)");
            sink.accept("");
            return;
        }

        // Mo rong truy van "tron" bang tu dien viet tat / dong nghia: ktx -> + "ký túc xá"
        List<String> expansions = plain ? extras.synonyms().expansions(raw) : List.of();
        if (!expansions.isEmpty()) {
            BooleanQuery.Builder b = new BooleanQuery.Builder();
            b.add(luceneQuery, BooleanClause.Occur.SHOULD);
            for (String e : expansions) {
                Query eq = expansionQuery(analyzer, parser, e);
                if (eq != null) b.add(new BoostQuery(eq, SYNONYM_BOOST), BooleanClause.Occur.SHOULD);
            }
            luceneQuery = b.build();
            parsed = luceneQuery.toString();
            sink.accept("  Mo rong truy van (viet tat / dong nghia): + " + String.join(" | ", expansions));
        }

        // Lay du ung vien de con du topK sau khi an ket qua gan trung
        TopDocs hits = searcher.search(luceneQuery, extras.dedup() > 0 ? topK * 3 + 10 : topK);
        long total = hits.totalHits.value;
        sink.accept("  Query da parse: " + parsed);
        sink.accept("  Tong so tai lieu khop: " + total
                + (total > topK ? "  (hien thi " + topK + " ket qua dau)" : ""));

        Set<Integer> shown = new HashSet<>();
        List<ScoreDoc> results = new ArrayList<>(List.of(hits.scoreDocs));
        if (extras.dedup() > 0 && results.size() > 1) {
            List<ScoreDoc> kept = new ArrayList<>();
            List<long[]> keptSketches = new ArrayList<>();
            int hidden = 0;
            for (ScoreDoc sd : results) {
                if (kept.size() == topK) break;
                long[] sk = NearDuplicates.sketch(storedFields.document(sd.doc).get("content"));
                boolean dup = false;
                for (long[] other : keptSketches) {
                    if (NearDuplicates.estimate(sk, other) >= extras.dedup()) {
                        dup = true;
                        break;
                    }
                }
                if (dup) {
                    hidden++;
                    shown.add(sd.doc); // khong hien lai o phan tf-idf ben duoi
                } else {
                    kept.add(sd);
                    keptSketches.add(sk);
                }
            }
            results = kept;
            if (hidden > 0) {
                sink.accept("  (da an " + hidden + " ket qua gan trung lap voi ket qua xep tren)");
            }
        } else if (results.size() > topK) {
            results = results.subList(0, topK);
        }

        if (extras.spell() != null && plain && total < SPELL_BELOW) {
            SpellSuggester.Suggestion s = extras.spell().suggest(raw);
            if (s != null) {
                sink.accept("  >> Co phai ban muon tim: \"" + s.text() + "\"  (" + s.hits()
                        + " van ban chua cum nay, so voi " + s.originalHits() + ")");
            }
        }

        if (results.isEmpty()) {
            sink.accept("  (khong co ket qua)");
            sink.accept("");
            return;
        }

        // tf-idf: cong thuc mac dinh cua Lucene (ClassicSimilarity) tren CHINH cac tu
        // duoc tach ra tu truy van (OR, khong boost cum/tu ghep nhu luceneQuery dung de
        // xep hang) - khong can lap chi muc lai vi norm duoc luu chung cho moi similarity.
        Query plainQuery = buildPlainQuery(analyzer, raw);

        Highlighter highlighter = buildHighlighter(luceneQuery, reader);
        int rank = 0;
        for (ScoreDoc sd : results) {
            Document d = storedFields.document(sd.doc);
            rank++;
            shown.add(sd.doc);
            double tfidf = tfidfSearcher.explain(plainQuery, sd.doc).getValue().doubleValue();
            sink.accept(String.format("  %2d. score=%.4f  tf-idf=%.4f  id=%s", rank, sd.score, tfidf, d.get("id")));
            describe(d, sink);
            sink.accept("      " + kwic(highlighter, analyzer, d.get("content")));
        }

        // luceneQuery (cum/tu ghep, dung de xep hang o tren) chat hon plainQuery (OR tung
        // tu). Nen mot so tai lieu KHONG khop luceneQuery van co the chua rieng le vai tu
        // khoa -> van tinh duoc tf-idf > 0. Liet ke rieng de nguoi dung thay het, khong bi
        // "an" chi vi khong khop cum chinh xac.
        if (!(plainQuery instanceof MatchNoDocsQuery)) {
            // shown co toi da topK tai lieu -> lay 2*topK la du topK tai lieu moi (khong duyet het chi muc)
            TopDocs plainHits = tfidfSearcher.search(plainQuery, 2 * topK);
            List<ScoreDoc> extra = new ArrayList<>();
            for (ScoreDoc sd : plainHits.scoreDocs) {
                if (!shown.contains(sd.doc) && extra.size() < topK) {
                    extra.add(sd);
                }
            }
            if (!extra.isEmpty()) {
                sink.accept("  -- Khong khop cum/tu ghep chinh xac, nhung co tu khoa rieng le (chi co tf-idf) --");
                int r2 = 0;
                for (ScoreDoc sd : extra) {
                    Document d = storedFields.document(sd.doc);
                    r2++;
                    sink.accept(String.format("  %2d. tf-idf=%.4f  id=%s", r2, sd.score, d.get("id")));
                    describe(d, sink);
                    sink.accept("      " + kwic(highlighter, analyzer, d.get("content")));
                }
            }
        }
        sink.accept("");
    }

    /** Cum mo rong: cum tieng Viet nhu truy van tron, con lai qua QueryParser. */
    private static Query expansionQuery(Analyzer analyzer, QueryParser parser, String text) throws IOException {
        if (analyzer instanceof VietnameseAnalyzer && text.contains(" ")) {
            Query q = buildVietnamesePhraseQuery(analyzer, text);
            return q instanceof MatchNoDocsQuery ? null : q;
        }
        try {
            return parser.parse(QueryParser.escape(text));
        } catch (ParseException e) {
            return null;
        }
    }

    /** In tieu de (neu co) va nguon cua 1 ket qua. */
    private static void describe(Document d, Consumer<String> sink) {
        String title = d.get("title");
        if (title != null && !title.isBlank()) {
            sink.accept("      title: " + title.replaceAll("\\s+", " ").strip());
        }
        sink.accept("      nguon: " + d.get("path"));
    }

    private static Highlighter buildHighlighter(Query query, IndexReader reader) throws IOException {
        // QueryScorer(query, reader, field): rewrite duoc ca prefix*/wildcard truoc khi lam noi bat.
        QueryScorer scorer = new QueryScorer(query, reader, DEFAULT_FIELD);
        Highlighter h = new Highlighter(new SimpleHTMLFormatter(MARK_A, MARK_B), scorer);
        h.setTextFragmenter(new SimpleSpanFragmenter(scorer, FRAGMENT_CHARS));
        return h;
    }

    /** Cat doan van ban quanh tu khoa (keyword-in-context); tu khop duoc danh dau de teeSink to sang. */
    private static String kwic(Highlighter highlighter, Analyzer analyzer, String text) {
        if (text == null || text.isBlank()) {
            return "";
        }
        try (TokenStream ts = analyzer.tokenStream(DEFAULT_FIELD, text)) {
            String frag = highlighter.getBestFragments(ts, text, MAX_FRAGMENTS, " … ");
            if (frag != null && !frag.isBlank()) {
                String cleaned = frag.replaceAll("\\s+", " ")
                        .replace(MARK_A + " " + MARK_B, "")  // bo danh dau rong (offset xap xi)
                        .replace(MARK_A + MARK_B, "")
                        .strip();
                if (!cleaned.isBlank()) {
                    return "… " + cleaned + " …";
                }
            }
        } catch (IOException | InvalidTokenOffsetsException e) {
            // roi xuong doan mac dinh
        }
        String flat = text.replaceAll("\\s+", " ").strip();
        return flat.length() <= FALLBACK_CHARS ? flat : flat.substring(0, FALLBACK_CHARS) + "...";
    }
}
