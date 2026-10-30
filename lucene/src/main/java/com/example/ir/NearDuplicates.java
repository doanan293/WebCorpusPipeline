package com.example.ir;

import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;

/**
 * An ket qua gan trung lap: van ban -> tap chuoi 4 tu lien tiep (shingle), do tuong dong = he so
 * Jaccard cua 2 tap, uoc luong nhanh bang MinHash (K ham bam, thanh phan thu i = min hash_i(s);
 * Jaccard ~ ti le thanh phan bang nhau). Cac trang khoa/vien hay dang lai cung 1 thong bao.
 *
 * Chi Jaccard thi khong du: noi dung luu ca khung trang (menu, ngay thang, tin lien quan), nen cung
 * 1 bai o /tin-tuc/, /en/news/, /savefile/ chi giong nhau 0.3-0.86, con 2 bai khac nhau cung site
 * co the giong toi 0.8. Vi vay 2 ket qua bi coi la trung khi:
 *   - Jaccard >= nguong (mac dinh 0.9), HOAC
 *   - cung tieu de VA (cung khoa url - doan cuoi duong dan - HOAC Jaccard >= {@value #SAME_TITLE_JACCARD}).
 */
final class NearDuplicates {

    static final int SHINGLE_WORDS = 4;
    static final int SKETCH_SIZE = 128;
    /** Nguong Jaccard khi 2 trang cung tieu de nhung khac khoa url (vd. dang lai o site khac). */
    static final double SAME_TITLE_JACCARD = 0.6;
    private static final long[] SEEDS = seeds(SKETCH_SIZE);

    private NearDuplicates() {}

    /** 1 ket qua de so trung: khoa url, tieu de chuan hoa, vec-to MinHash cua noi dung. */
    record Item(String key, String title, long[] sketch) {
        static Item of(String url, String title, String content) {
            return new Item(urlKey(url), normTitle(title), NearDuplicates.sketch(content));
        }
    }

    static boolean duplicate(Item a, Item b, double threshold) {
        double j = estimate(a.sketch(), b.sketch());
        if (j >= threshold) return true;
        if (a.title().isEmpty() || !a.title().equals(b.title())) return false;
        return (!a.key().isEmpty() && a.key().equals(b.key())) || j >= SAME_TITLE_JACCARD;
    }

    /**
     * Doan cuoi cua duong dan (kem query), bo ".html" va ma so bai dai o cuoi:
     *   ts.hust.edu.vn/en/news/thong-bao-x, ts.hust.edu.vn/tin-tuc/thong-bao-x -> thong-bao-x
     *   hust.edu.vn/vi/news/savefile/a/bai-y-651867.html -> bai-y
     * Doan qua ngan (index, lien-he, page/2...) thi khong dung lam khoa.
     */
    static String urlKey(String url) {
        if (url == null) return "";
        String u = url.toLowerCase(Locale.ROOT).replaceFirst("#.*$", "");
        int q = u.indexOf('?');
        String query = q < 0 ? "" : u.substring(q);
        String path = (q < 0 ? u : u.substring(0, q)).replaceAll("/+$", "");
        String last = path.substring(path.lastIndexOf('/') + 1)
                .replaceFirst("\\.(html?|php|aspx?)$", "")
                .replaceFirst("-\\d{5,}$", "");
        return last.length() < 12 ? "" : last + query;
    }

    private static String normTitle(String title) {
        return title == null ? "" : java.text.Normalizer.normalize(title, java.text.Normalizer.Form.NFC)
                .toLowerCase(Locale.ROOT).replaceAll("\\s+", " ").strip();
    }

    /** Vec-to khung MinHash cua tap chuoi 4 tu. */
    static long[] sketch(String text) {
        long[] sk = new long[SKETCH_SIZE];
        Arrays.fill(sk, Long.MAX_VALUE);
        for (String s : shingles(text)) {
            long h = fnv64(s);
            for (int i = 0; i < SKETCH_SIZE; i++) {
                long v = mix64(h ^ SEEDS[i]);
                if (v < sk[i]) sk[i] = v;
            }
        }
        return sk;
    }

    static double estimate(long[] a, long[] b) {
        int same = 0;
        for (int i = 0; i < a.length; i++) if (a[i] == b[i]) same++;
        return (double) same / a.length;
    }

    private static Set<String> shingles(String text) {
        String[] w = Arrays.stream((text == null ? "" : text).toLowerCase(Locale.ROOT).split("[^\\p{L}\\p{N}]+"))
                .filter(s -> !s.isEmpty()).toArray(String[]::new);
        Set<String> out = new LinkedHashSet<>();
        for (int i = 0; i + SHINGLE_WORDS <= w.length; i++) {
            out.add(String.join(" ", Arrays.copyOfRange(w, i, i + SHINGLE_WORDS)));
        }
        if (out.isEmpty() && w.length > 0) out.add(String.join(" ", w)); // van ban rat ngan
        return out;
    }

    private static long fnv64(String s) {
        long h = 0xcbf29ce484222325L;
        for (int i = 0; i < s.length(); i++) {
            h ^= s.charAt(i);
            h *= 0x100000001b3L;
        }
        return h;
    }

    /** SplitMix64: moi seed cho 1 ham bam doc lap. */
    private static long mix64(long z) {
        z = (z ^ (z >>> 30)) * 0xbf58476d1ce4e5b9L;
        z = (z ^ (z >>> 27)) * 0x94d049bb133111ebL;
        return z ^ (z >>> 31);
    }

    private static long[] seeds(int k) {
        long[] s = new long[k];
        long x = 42;
        for (int i = 0; i < k; i++) {
            x += 0x9e3779b97f4a7c15L;
            s[i] = mix64(x);
        }
        return s;
    }
}
