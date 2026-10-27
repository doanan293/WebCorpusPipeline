package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.CharArraySet;
import org.apache.lucene.analysis.LowerCaseFilter;
import org.apache.lucene.analysis.StopFilter;
import org.apache.lucene.analysis.TokenStream;
import org.apache.lucene.analysis.Tokenizer;
import org.apache.lucene.analysis.miscellaneous.ASCIIFoldingFilter;
import org.apache.lucene.analysis.tokenattributes.CharTermAttribute;
import org.apache.lucene.analysis.tokenattributes.OffsetAttribute;

import java.io.IOException;
import java.io.Reader;
import java.io.UncheckedIOException;
import java.util.Arrays;
import java.util.List;

/**
 * Analyzer tieng Viet. Chuoi xu ly:
 *
 *   VnWordTokenizer     tach TU bang VnCoreNLP  -> "tri_tue_nhan_tao", "xu_ly"
 *   LowerCaseFilter     chu thuong
 *   StopFilter          bo tu chuc nang + dau cau  (va, cua, la, ",", "." ...)
 *   ASCIIFoldingFilter  bo dau: "tiếng" -> "tieng"  (tim co dau / khong dau deu duoc)
 *   CompoundSplitFilter tu ghep -> them tung am tiet cung vi tri  (tang recall)
 *
 * Ket qua: token chinh la TU (khong phai am tiet), nhung chi muc cung chua tung
 * am tiet cua tu ghep va khong phan biet dau -> it bi truot khi truy van/tai lieu
 * bi bo tach tu chia cum khac nhau.
 */
final class VietnameseAnalyzer extends Analyzer {

    /** Tu dung + dau cau (viet co dau, chu thuong - StopFilter chay TRUOC ASCIIFolding). */
    static final CharArraySet STOP = new CharArraySet(Arrays.asList(
            "và", "của", "là", "các", "được", "cho", "những", "đã", "khi", "với",
            "để", "một", "có", "không", "này", "đó", "cũng", "thì", "mà", "ở",
            "nên", "hay", "còn", "sẽ", "vào", "ra", "rằng", "bị", "theo", "về",
            "như", "cùng", "hơn", "đến", "vì", "do", "nếu",
            ",", ".", ";", ":", "\"", "'", "(", ")", "[", "]", "-", "–", "/", "?", "!", "…"),
            true);

    private final VnSegmenter segmenter;

    VietnameseAnalyzer(VnSegmenter segmenter) {
        this.segmenter = segmenter;
    }

    /** Ket qua tach TU tho (truoc lowercase / bo tu dung / bo dau), dung cho che do "segment". */
    List<String> segment(String text) {
        return segmenter.segment(text);
    }

    @Override
    protected TokenStreamComponents createComponents(String fieldName) {
        Tokenizer source = new VnWordTokenizer(segmenter);
        TokenStream ts = new LowerCaseFilter(source);
        ts = new StopFilter(ts, STOP);
        ts = new ASCIIFoldingFilter(ts);
        ts = new CompoundSplitFilter(ts);
        return new TokenStreamComponents(source, ts);
    }

    /**
     * Chuan hoa cho truy van KHONG qua tach tu: ky tu dai dien (tuyển*), mo ho (hoc~), khoang
     * [a TO b]. Phai giong cac buoc chuan hoa luc lap chi muc (chu thuong + bo dau), neu khong
     * "tuyển*" se khong khop term "tuyen_sinh" trong chi muc.
     */
    @Override
    protected TokenStream normalize(String fieldName, TokenStream in) {
        return new ASCIIFoldingFilter(new LowerCaseFilter(in));
    }

    @Override
    public void close() {
        try {
            segmenter.close();
        } catch (IOException ignored) {
            // dong best-effort
        }
        super.close();
    }

    /** Doc het input, tach tu 1 lan trong reset(), roi phat tung token kem offset xap xi. */
    private static final class VnWordTokenizer extends Tokenizer {

        private final VnSegmenter segmenter;
        private final CharTermAttribute termAtt = addAttribute(CharTermAttribute.class);
        private final OffsetAttribute offsetAtt = addAttribute(OffsetAttribute.class);

        private List<String> forms = List.of();
        private int pos;
        private int cursor;
        private String text = "";
        private String lower = "";
        private int finalOffset;
        private int lastStart;

        VnWordTokenizer(VnSegmenter segmenter) {
            this.segmenter = segmenter;
        }

        @Override
        public void reset() throws IOException {
            super.reset();
            pos = 0;
            cursor = 0;
            finalOffset = 0;
            lastStart = 0;
            text = readAll(input);
            lower = lowerSameLength(text);
            try {
                forms = segmenter.segment(text);
            } catch (UncheckedIOException e) {
                throw e.getCause();
            }
        }

        @Override
        public boolean incrementToken() {
            if (pos >= forms.size()) {
                return false;
            }
            clearAttributes();
            String form = forms.get(pos++);
            termAtt.append(form);

            int[] span = locateSpan(form);
            int start;
            int end;
            if (span == null) {
                start = Math.min(cursor, text.length());
                end = start;
                cursor = Math.min(cursor + form.length(), text.length());
            } else {
                start = span[0];
                end = span[1];
                cursor = end;
            }
            // offset khong duoc di lui va khong vuot qua van ban (IndexWriter se tu choi ca tai lieu)
            start = Math.min(Math.max(start, lastStart), text.length());
            end = Math.min(Math.max(end, start), text.length());
            lastStart = start;
            finalOffset = Math.max(finalOffset, end);
            offsetAtt.setOffset(correctOffset(start), correctOffset(end));
            return true;
        }

        private int[] locateSpan(String form) {
            String[] sylls = lowerSameLength(form).split("_");
            if (sylls.length == 0 || sylls[0].isEmpty()) {
                return null;
            }
            int s = lower.indexOf(sylls[0], cursor);
            if (s < 0) {
                return null;
            }
            int e = s + sylls[0].length();
            for (int k = 1; k < sylls.length; k++) {
                int ns = lower.indexOf(sylls[k], e);
                if (ns < 0 || ns - e > 3) {
                    break;
                }
                e = ns + sylls[k].length();
            }
            return new int[]{s, e};
        }

        @Override
        public void end() throws IOException {
            super.end();
            offsetAtt.setOffset(correctOffset(finalOffset), correctOffset(finalOffset));
        }

        @Override
        public void close() throws IOException {
            super.close();
            forms = List.of();
            text = "";
            lower = "";
        }

        /**
         * Chu thuong TUNG KY TU, giu nguyen do dai. String.toLowerCase() co the lam chuoi dai ra
         * ("İ" -> "i" + dau cham ket hop), khi do vi tri tim trong ban chu thuong lech khoi van ban
         * goc va offset di lui -> IndexWriter tu choi ca tai lieu.
         */
        private static String lowerSameLength(String s) {
            char[] cs = s.toCharArray();
            for (int i = 0; i < cs.length; i++) {
                cs[i] = Character.toLowerCase(cs[i]);
            }
            return new String(cs);
        }

        private static String readAll(Reader r) throws IOException {
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[2048];
            int n;
            while ((n = r.read(buf)) != -1) {
                sb.append(buf, 0, n);
            }
            return sb.toString();
        }
    }
}
