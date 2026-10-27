package com.example.ir;

import org.apache.lucene.analysis.miscellaneous.ASCIIFoldingFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Mo rong truy van bang tu dien dong nghia / viet tat (file synonyms.txt). Moi dong la 1 nhom tu
 * tuong duong, ngan cach bang dau phay:
 *
 *   ký túc xá, ktx
 *   công nghệ thông tin, cntt
 *
 * Neu truy van chua 1 thanh vien cua nhom (so khop khong dau, nguyen tu) thi cac thanh vien con lai
 * duoc them vao truy van nhu cac lua chon OR co trong so thap hon.
 */
final class Synonyms {

    private final List<List<String>> groups;

    private Synonyms(List<List<String>> groups) {
        this.groups = groups;
    }

    static Synonyms empty() {
        return new Synonyms(List.of());
    }

    static Synonyms load(Path file) throws IOException {
        List<List<String>> groups = new ArrayList<>();
        for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
            line = line.strip();
            if (line.isEmpty() || line.startsWith("#")) continue;
            List<String> g = new ArrayList<>();
            for (String w : line.split(",")) {
                w = Normalizer.normalize(w.strip(), Normalizer.Form.NFC);
                if (!w.isEmpty()) g.add(w);
            }
            if (g.size() > 1) groups.add(List.copyOf(g));
        }
        return new Synonyms(List.copyOf(groups));
    }

    int size() {
        return groups.size();
    }

    /** Cac cum tu them vao truy van: thanh vien con lai cua moi nhom co thanh vien xuat hien trong query. */
    List<String> expansions(String query) {
        String q = " " + fold(query) + " ";
        Set<String> out = new LinkedHashSet<>();
        for (List<String> g : groups) {
            boolean hit = false;
            for (String w : g) {
                if (containsWord(q, fold(w))) {
                    hit = true;
                    break;
                }
            }
            if (!hit) continue;
            for (String w : g) {
                if (!containsWord(q, fold(w))) out.add(w);
            }
        }
        return List.copyOf(out);
    }

    private static boolean containsWord(String paddedText, String word) {
        return !word.isEmpty()
                && Pattern.compile("[^\\p{L}\\p{N}]" + Pattern.quote(word) + "[^\\p{L}\\p{N}]").matcher(paddedText).find();
    }

    /** Chu thuong + bo dau + gop khoang trang: "Ký  Túc Xá" -> "ky tuc xa". */
    static String fold(String s) {
        String lower = Normalizer.normalize(s, Normalizer.Form.NFC).toLowerCase(Locale.ROOT);
        char[] in = lower.toCharArray();
        char[] out = new char[in.length * 4];
        int n = ASCIIFoldingFilter.foldToASCII(in, 0, out, 0, in.length);
        return new String(out, 0, n).replaceAll("[^\\p{L}\\p{N}]+", " ").strip();
    }
}
