package com.example.ir;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;

/** Quet 1 thu muc, rut van ban tung file -> danh sach document. */
public final class DocLoader {

    /**
     * 1 van ban dau vao (tu thu muc hoac tu MongoDB).
     *   id      : khoa duy nhat - duong dan tuong doi (file) hoac URL (Mongo)
     *   source  : noi lay van ban - duong dan file, hoac "mongo:html" / "mongo:other (pdf)"
     *   title   : tieu de; ten file voi file
     *   intro   : van ban cac lien ket tro toi trang (anchor text); "" neu khong co
     */
    public record Doc(String id, String source, String title, String intro, String content) {
        /**
         * Chuan hoa Unicode NFC (nhu QueryLoader.Query): van ban web co the o dang NFD (nguyen am +
         * dau ket hop rieng), ASCIIFoldingFilter khong bo duoc dau dang do -> "tie\u0301ng" khong khop
         * truy van "tiếng". Chuan hoa 1 lan o day thi moi nguon (file, Mongo) deu duoc bao ve.
         */
        public Doc {
            title = nfc(title);
            intro = nfc(intro);
            content = nfc(content);
        }
    }

    static String nfc(String s) {
        return s == null || Normalizer.isNormalized(s, Normalizer.Form.NFC) ? s : Normalizer.normalize(s, Normalizer.Form.NFC);
    }

    private DocLoader() {}

    public static List<Doc> load(Path docsDir) throws IOException {
        if (!Files.isDirectory(docsDir)) {
            throw new IOException("Thu muc van ban khong ton tai: " + docsDir.toAbsolutePath());
        }
        List<Doc> docs = new ArrayList<>();
        try (Stream<Path> walk = Files.walk(docsDir)) {
            List<Path> files = walk
                    .filter(Files::isRegularFile)
                    .filter(TextExtractor::isSupported)
                    .sorted(Comparator.comparing(Path::toString))
                    .toList();
            for (Path file : files) {
                String content;
                try {
                    content = TextExtractor.extract(file);
                } catch (IOException e) {
                    System.err.println("  [bo qua] khong doc duoc " + file + ": " + e.getMessage());
                    continue;
                }
                if (content == null || content.isBlank()) {
                    System.err.println("  [bo qua] rong hoac khong ho tro: " + file);
                    continue;
                }
                String id = docsDir.relativize(file).toString();
                docs.add(new Doc(id, file.toString(), file.getFileName().toString(), "", content));
            }
        } catch (UncheckedIOException e) {
            throw e.getCause();
        }
        return docs;
    }
}
