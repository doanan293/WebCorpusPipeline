package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.LowerCaseFilter;
import org.apache.lucene.analysis.TokenStream;
import org.apache.lucene.analysis.Tokenizer;
import org.apache.lucene.analysis.standard.StandardTokenizer;
import org.apache.lucene.index.IndexReader;
import org.apache.lucene.index.MultiTerms;
import org.apache.lucene.index.Term;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.PhraseQuery;
import org.apache.lucene.search.spell.DirectSpellChecker;
import org.apache.lucene.search.spell.SuggestMode;
import org.apache.lucene.search.spell.SuggestWord;
import org.apache.lucene.search.spell.SuggestWordFrequencyComparator;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Goi y "Co phai ban muon tim ..." cho truy van go sai.
 *
 * Bo tu vung: field {@value FIELD} - cac AM TIET con nguyen dau cua title + content.
 *   1. Voi moi am tiet cua truy van, lay cac am tiet trong bo tu vung cach no <= 1 (am tiet
 *      <= 3 ky tu) hoac <= 2 phep sua (Levenshtein), uu tien am tiet pho bien nhat.
 *   2. Thay lan luot tung am tiet bang ung vien cua no, dem so van ban chua nguyen cum (PhraseQuery
 *      tren am tiet da bo dau cua field content), giu phuong an tot nhat; lap toi da 2 luot:
 *      "sử lý ảnh" -> "xử lý ảnh" vi cum sau xuat hien nhieu hon han, du "sử" tu no viet dung.
 * Chi goi y khi cum goc khong xuat hien, hoac cum tot nhat nhieu gap >= 10 lan cum goc.
 */
final class SpellSuggester {

    static final String FIELD = "spell";
    private static final int CANDIDATES_PER_WORD = 8;
    private static final int MAX_WORDS = 8;
    private static final int MAX_ROUNDS = 2;

    record Suggestion(String text, long hits, long originalHits) {}

    private final IndexReader reader;
    private final IndexSearcher searcher;
    private final DirectSpellChecker checker = new DirectSpellChecker();

    SpellSuggester(IndexReader reader) {
        this.reader = reader;
        this.searcher = new IndexSearcher(reader);
        checker.setMinPrefix(0);          // "sử" -> "xử": khac ngay ky tu dau
        checker.setMinQueryLength(2);
        checker.setAccuracy(0.4f);
        checker.setLowerCaseTerms(false); // field spell da chu thuong
        checker.setComparator(new SuggestWordFrequencyComparator());
        // mac dinh: tu co trong > 1% van ban coi la viet dung, khong goi y -> "sử" (lịch sử, sử dụng)
        // khong bao gio duoc thay. Viec chon dung/sai da lam o buoc kiem tra trong ngu canh.
        checker.setMaxQueryFrequency(1e9f);
    }

    /** Analyzer cho field spell: tach am tiet + chu thuong, GIU dau. */
    static Analyzer analyzer() {
        return new Analyzer() {
            @Override
            protected TokenStreamComponents createComponents(String fieldName) {
                Tokenizer src = new StandardTokenizer();
                TokenStream ts = new LowerCaseFilter(src);
                return new TokenStreamComponents(src, ts);
            }
        };
    }

    /** Chi muc cu (lap truoc khi co field spell) thi khong goi y duoc. */
    boolean available() throws IOException {
        return MultiTerms.getTerms(reader, FIELD) != null;
    }

    /** null neu khong co goi y tot hon truy van goc. */
    Suggestion suggest(String query) throws IOException {
        List<String> ws = new ArrayList<>();
        for (String w : query.toLowerCase(Locale.ROOT).split("[^\\p{L}\\p{N}]+")) {
            if (!w.isEmpty()) ws.add(w);
        }
        if (ws.isEmpty() || ws.size() > MAX_WORDS) return null;

        // ung vien cho tung am tiet: cac am tiet cach <= 1-2 phep sua, pho bien nhat truoc. Khong
        // doi "pho bien hon" vi tu dung chua chac pho bien hon tu sai (sử / xử) - ngu canh quyet dinh.
        List<List<String>> alternatives = new ArrayList<>();
        for (String w : ws) {
            List<String> alts = new ArrayList<>();
            if (w.length() >= 2 && !w.chars().allMatch(Character::isDigit)) {
                checker.setMaxEdits(w.length() <= 3 ? 1 : 2);
                for (SuggestWord s : checker.suggestSimilar(new Term(FIELD, w), CANDIDATES_PER_WORD, reader,
                        SuggestMode.SUGGEST_ALWAYS)) {
                    alts.add(s.string);
                }
            }
            alternatives.add(alts);
        }

        // Kiem tra trong ngu canh: moi luot thay 1 am tiet, giu phuong an co nhieu van ban chua
        // nguyen cum nhat; toi da MAX_ROUNDS luot (sua duoc ca 2 loi trong 1 truy van).
        long original = count(ws);
        List<String> best = ws;
        long bestHits = original;
        for (int round = 0; round < MAX_ROUNDS; round++) {
            List<String> roundBest = null;
            long roundHits = bestHits;
            for (int i = 0; i < best.size(); i++) {
                for (String alt : alternatives.get(i)) {
                    if (alt.equals(best.get(i))) continue;
                    List<String> c = new ArrayList<>(best);
                    c.set(i, alt);
                    long hits = count(c);
                    if (hits > roundHits) {
                        roundHits = hits;
                        roundBest = c;
                    }
                }
            }
            if (roundBest == null) break;
            best = roundBest;
            bestHits = roundHits;
        }
        // Goi y khi cum moi vuot han: cum goc khong co trong van ban nao, hoac cum moi nhieu gap
        // >= 10 lan (cum goc co that, vd. viet tat "cntt", thi khong doi sang tu khac)
        if (best == ws || (original > 0 && bestHits < 10 * original)) return null;
        return new Suggestion(String.join(" ", best), bestHits, original);
    }

    /** So van ban chua chuoi am tiet (bo dau, bo tu dung) lien tiep trong field content. */
    private long count(List<String> syllables) throws IOException {
        PhraseQuery.Builder pb = new PhraseQuery.Builder();
        pb.setSlop(1); // tu dung bi loai khoi chi muc de lai 1 vi tri trong
        int n = 0;
        for (String s : syllables) {
            if (VietnameseAnalyzer.STOP.contains(s)) continue;
            pb.add(new Term(Searcher.DEFAULT_FIELD, Synonyms.fold(s)));
            n++;
        }
        return n == 0 ? 0 : searcher.count(pb.build());
    }
}
