package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.miscellaneous.PerFieldAnalyzerWrapper;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.FieldType;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.IndexOptions;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.Map;

/** Tao chi muc Lucene tren dia tu danh sach document (doc kieu stream, khong can nap het vao RAM). */
public final class Indexer {

    private static final int PROGRESS_EVERY = 1000;   // in tien do moi N tai lieu
    private static final double RAM_BUFFER_MB = 256;  // gom nhieu tai lieu truoc khi ghi segment

    /** Field spell: chi can biet am tiet co trong van ban nao (bo tu vung + df), khong can tan suat / vi tri. */
    private static final FieldType SPELL_TYPE = new FieldType();

    static {
        SPELL_TYPE.setIndexOptions(IndexOptions.DOCS);
        SPELL_TYPE.setTokenized(true);
        SPELL_TYPE.setStored(false);
        SPELL_TYPE.setOmitNorms(true);
        SPELL_TYPE.freeze();
    }

    private Indexer() {}

    public static int build(Path indexDir, Iterable<DocLoader.Doc> docs, Analyzer analyzer) throws IOException {
        try (Directory dir = FSDirectory.open(indexDir);
             Analyzer spellAnalyzer = SpellSuggester.analyzer()) {
            // field spell dung analyzer rieng (giu dau), cac field con lai dung analyzer chinh
            Analyzer perField = new PerFieldAnalyzerWrapper(analyzer, Map.of(SpellSuggester.FIELD, spellAnalyzer));
            IndexWriterConfig cfg = new IndexWriterConfig(perField);
            cfg.setOpenMode(IndexWriterConfig.OpenMode.CREATE); // tao moi moi lan chay
            cfg.setRAMBufferSizeMB(RAM_BUFFER_MB);
            try (IndexWriter writer = new IndexWriter(dir, cfg)) {
                long start = System.nanoTime();
                int n = 0;
                for (DocLoader.Doc d : docs) {
                    try {
                        writer.addDocument(toLuceneDoc(d));
                    } catch (IllegalArgumentException e) {
                        // vd. 1 token dai hon 32766 byte: bo tai lieu nay, khong dung ca qua trinh
                        System.err.println("  [bo qua] " + d.id() + ": " + e.getMessage());
                        continue;
                    }
                    if (++n % PROGRESS_EVERY == 0) {
                        double secs = (System.nanoTime() - start) / 1e9;
                        System.out.printf("  ... %,d tai lieu  (%.0f s, %.1f tl/s)%n", n, secs, n / secs);
                    }
                }
                writer.commit();
                return writer.getDocStats().numDocs;
            }
        }
    }

    private static Document toLuceneDoc(DocLoader.Doc d) {
        Document doc = new Document();
        // id: khong phan tich, luu lai de hien thi / tra cuu (duong dan file hoac URL)
        doc.add(new StringField("id", d.id(), Field.Store.YES));
        doc.add(new StoredField("path", d.source()));
        // title: co phan tich (tim theo tieu de / ten file), luu de hien thi
        if (!d.title().isBlank()) {
            doc.add(new TextField("title", d.title(), Field.Store.YES));
        }
        // intro: anchor text cua cac lien ket tro toi trang - chi de tim, khong can luu
        if (!d.intro().isBlank()) {
            doc.add(new TextField("intro", d.intro(), Field.Store.NO));
        }
        // content: phan tich + tim kiem, VA luu lai ban goc.
        // Luu ban goc de Highlighter cat duoc doan chua tu khoa (KWIC) hien thi.
        doc.add(new TextField("content", d.content(), Field.Store.YES));
        // spell: bo tu vung CON DAU cho goi y sua loi chinh ta
        doc.add(new Field(SpellSuggester.FIELD, d.title() + "\n" + d.content(), SPELL_TYPE));
        return doc;
    }
}
