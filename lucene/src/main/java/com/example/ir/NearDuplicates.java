package com.example.ir;

import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;

/**
 * An ket qua gan trung lap: van ban -> tap chuoi 4 tu lien tiep (shingle), do tuong dong = he so
 * Jaccard cua 2 tap, uoc luong nhanh bang MinHash (K ham bam, thanh phan thu i = min hash_i(s);
 * Jaccard ~ ti le thanh phan bang nhau). Cac trang khoa/vien hay dang lai cung 1 thong bao.
 */
final class NearDuplicates {

    static final int SHINGLE_WORDS = 4;
    static final int SKETCH_SIZE = 128;
    private static final long[] SEEDS = seeds(SKETCH_SIZE);

    private NearDuplicates() {}

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
