package com.example.ir;

import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.Projections;
import org.apache.lucene.index.DocValues;
import org.apache.lucene.index.LeafReaderContext;
import org.apache.lucene.index.NumericDocValues;
import org.apache.lucene.search.DoubleValues;
import org.apache.lucene.search.DoubleValuesSource;
import org.apache.lucene.search.IndexSearcher;
import org.bson.Document;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * PageRank tren do thi lien ket cua crawler (collection graph), dung lam diem chat luong tinh g(d).
 *
 *   Dinh   : moi trang html va moi tep other.
 *   Canh   : lien ket src -> dest giua 2 dinh, CHI KHI 2 dau khac host. ~85% lien ket la trong cung
 *            1 site (menu, chan trang, phan trang); tinh ca vao thi cac trang dieu huong cua thu vien so
 *            dlib.hust.edu.vn chiem het top. Lien ket tu site khac moi la "loi gioi thieu" thuc su.
 *   Tinh   : phuong phap luy thua, buoc nhay alpha = 0.1; trang khong co lien ket ra nhay deu toi moi trang.
 *   g(d)   : chuan hoa ve [0, 1] theo thang log: 0 = trang khong co lien ket nao tu site khac,
 *            1 = trang co PageRank cao nhat.
 *
 * Luu trong chi muc thanh doc values {@value FIELD} (ghi de duoc tai cho, khong phai lap lai tai lieu).
 */
final class PageRank {

    static final String FIELD = "pagerank";
    static final double ALPHA = 0.1;
    private static final int MAX_ITER = 200;
    private static final double TOL = 1e-10;

    /** g theo url, kem thong ke de in ra. */
    record Result(Map<String, Float> scores, int nodes, int edges, int linked, int iterations, List<String> top) {}

    private PageRank() {}

    static Result compute(MongoDatabase db) {
        Map<String, Integer> id = new HashMap<>();
        List<String> urls = new ArrayList<>();
        for (String c : List.of("html", "other")) {
            for (Document d : db.getCollection(c).find()
                    .projection(Projections.fields(Projections.include("url"), Projections.excludeId())).batchSize(10_000)) {
                String u = d.getString("url");
                if (u != null && id.putIfAbsent(u, urls.size()) == null) urls.add(u);
            }
        }
        int n = urls.size();
        String[] hosts = urls.stream().map(PageRank::host).toArray(String[]::new);
        List<Long> pairs = new ArrayList<>();
        for (Document e : db.getCollection("graph").find()
                .projection(Projections.fields(Projections.include("src", "dest"), Projections.excludeId())).batchSize(20_000)) {
            Integer s = id.get(e.getString("src"));
            Integer d = id.get(e.getString("dest"));
            if (s != null && d != null && !hosts[s].equals(hosts[d])) pairs.add(((long) s << 32) | d);
        }
        long[] edges = pairs.stream().mapToLong(Long::longValue).distinct().sorted().toArray();

        // phuong phap luy thua: x' = (alpha * S_mo + S_dong)/N + (1 - alpha) * sum_{i -> j} x_i / bacRa(i)
        int[] deg = new int[n];
        boolean[] linked = new boolean[n];
        for (long e : edges) {
            deg[(int) (e >>> 32)]++;
            linked[(int) e] = true;
        }
        double[] x = new double[n];
        Arrays.fill(x, 1.0 / n);
        int it = 0;
        while (it < MAX_ITER) {
            it++;
            double[] next = new double[n];
            double open = 0, dangling = 0;
            for (int i = 0; i < n; i++) {
                if (deg[i] == 0) dangling += x[i];
                else open += x[i];
            }
            for (long e : edges) {
                int s = (int) (e >>> 32);
                next[(int) e] += (1 - ALPHA) * x[s] / deg[s];
            }
            double base = (ALPHA * open + dangling) / n;
            double diff = 0;
            for (int j = 0; j < n; j++) {
                next[j] += base;
                diff += Math.abs(next[j] - x[j]);
            }
            x = next;
            if (diff < TOL) break;
        }

        // chuan hoa log: trang khong duoc tro toi tu site khac co PageRank nho nhat (= muc nen) -> 0
        double floor = Double.MAX_VALUE, max = 0;
        int nLinked = 0;
        for (int i = 0; i < n; i++) {
            floor = Math.min(floor, x[i]);
            max = Math.max(max, x[i]);
            if (linked[i]) nLinked++;
        }
        double span = Math.log(max / floor);
        Map<String, Float> g = new HashMap<>(n * 2);
        Integer[] order = new Integer[n];
        for (int i = 0; i < n; i++) {
            order[i] = i;
            g.put(urls.get(i), span <= 0 ? 0f : (float) Math.max(0, Math.log(x[i] / floor) / span));
        }
        final double[] pr = x;
        Arrays.sort(order, (a, b) -> Double.compare(pr[b], pr[a]));
        List<String> top = new ArrayList<>();
        for (int i = 0; i < Math.min(5, n); i++) top.add(String.format("%.3f  %s", g.get(urls.get(order[i])), urls.get(order[i])));
        return new Result(g, n, edges.length, nLinked, it, top);
    }

    static String host(String url) {
        int a = url.indexOf("//");
        int start = a < 0 ? 0 : a + 2;
        int end = url.indexOf('/', start);
        return (end < 0 ? url.substring(start) : url.substring(start, end)).toLowerCase();
    }

    /** g(d) cua 1 tai lieu (0 neu chua co). */
    static float of(IndexSearcher searcher, int doc) throws IOException {
        List<LeafReaderContext> leaves = searcher.getIndexReader().leaves();
        LeafReaderContext leaf = leaves.get(org.apache.lucene.index.ReaderUtil.subIndex(doc, leaves));
        NumericDocValues dv = DocValues.getNumeric(leaf.reader(), FIELD);
        return dv.advanceExact(doc - leaf.docBase) ? Float.intBitsToFloat((int) dv.longValue()) : 0f;
    }

    /** He so nhan vao diem BM25: 1 + w * g(d) (FunctionScoreQuery.boostByValue). */
    static DoubleValuesSource boost(float weight) {
        return new Boost(weight);
    }

    private static final class Boost extends DoubleValuesSource {
        private final float weight;

        Boost(float weight) {
            this.weight = weight;
        }

        @Override
        public DoubleValues getValues(LeafReaderContext ctx, DoubleValues scores) throws IOException {
            NumericDocValues dv = DocValues.getNumeric(ctx.reader(), FIELD);
            return new DoubleValues() {
                private double value = 1;

                @Override
                public double doubleValue() {
                    return value;
                }

                @Override
                public boolean advanceExact(int doc) throws IOException {
                    value = 1 + (dv.advanceExact(doc) ? weight * Float.intBitsToFloat((int) dv.longValue()) : 0);
                    return true;
                }
            };
        }

        @Override
        public boolean needsScores() {
            return false;
        }

        @Override
        public DoubleValuesSource rewrite(IndexSearcher searcher) {
            return this;
        }

        @Override
        public boolean isCacheable(LeafReaderContext ctx) {
            return DocValues.isCacheable(ctx, FIELD);
        }

        @Override
        public int hashCode() {
            return Float.hashCode(weight);
        }

        @Override
        public boolean equals(Object o) {
            return o instanceof Boost b && b.weight == weight;
        }

        @Override
        public String toString() {
            return "1+" + weight + "*pagerank";
        }
    }
}
