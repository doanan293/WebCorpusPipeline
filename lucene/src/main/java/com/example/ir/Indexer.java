package com.example.ir;

import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.miscellaneous.PerFieldAnalyzerWrapper;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.FieldType;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexOptions;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.index.StoredFields;
import org.apache.lucene.index.Term;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.TermQuery;
import org.apache.lucene.search.TopDocs;
import org.apache.lucene.store.Directory;
import org.apache.lucene.store.FSDirectory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Map;
import java.util.Set;

/**
 * Tao chi muc Lucene tren dia tu danh sach document (doc kieu stream, khong can nap het vao RAM).
 *
 *   build : lap moi toan bo (xoa chi muc cu).
 *   sync  : dong bo tang dan - them / ghi de / xoa theo id (url), bo qua van ban khong doi
 *           (so chu ky {@value #SIG} cua title + intro + content). Moc thoi gian da dong bo luu ngay
 *           trong commit cua chi muc ({@value #SINCE}), nen chi muc va moc luon khop nhau.
 */
public final class Indexer {

    /** Khoa trong commit cua chi muc: updated_at tu do lan dong bo sau doc tiep. */
    static final String SINCE = "mongo_since";
    private static final String SIG = "sig";

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

    /** Ket qua 1 luot dong bo. */
    record SyncStats(int added, int updated, int deleted, int unchanged) {}

    private Indexer() {}

    public static int build(Path indexDir, Iterable<DocLoader.Doc> docs, Analyzer analyzer) throws IOException {
        return build(indexDir, docs, analyzer, null);
    }

    /** @param since moc updated_at ghi vao commit de --mode sync doc tiep (null: lap tu thu muc, khong co moc) */
    public static int build(Path indexDir, Iterable<DocLoader.Doc> docs, Analyzer analyzer, String since)
            throws IOException {
        try (Directory dir = FSDirectory.open(indexDir);
             Analyzer spellAnalyzer = SpellSuggester.analyzer();
             IndexWriter writer = new IndexWriter(dir, config(analyzer, spellAnalyzer, IndexWriterConfig.OpenMode.CREATE))) {
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
            if (since != null) {
                writer.setLiveCommitData(Map.of(SINCE, since).entrySet());
            }
            writer.commit();
            return writer.getDocStats().numDocs;
        }
    }

    /**
     * Dua cac thay doi vao chi muc co san: doc = null -> xoa; id chua co -> them; id da co ma chu ky
     * khac -> ghi de (updateDocument = xoa ban cu + them ban moi); chu ky giong -> bo qua.
     * Ghi moc newSince va commit 1 lan o cuoi (tien trinh chet giua chung thi luot sau lam lai tu moc cu).
     */
    static SyncStats sync(Path indexDir, Iterable<MongoDocLoader.Change> changes, Analyzer analyzer,
                          String newSince) throws IOException {
        int added = 0, updated = 0, deleted = 0, unchanged = 0;
        try (Directory dir = FSDirectory.open(indexDir);
             Analyzer spellAnalyzer = SpellSuggester.analyzer();
             IndexWriter writer = new IndexWriter(dir, config(analyzer, spellAnalyzer, IndexWriterConfig.OpenMode.APPEND));
             DirectoryReader reader = DirectoryReader.open(writer)) {
            IndexSearcher searcher = new IndexSearcher(reader);
            StoredFields stored = searcher.storedFields();
            for (MongoDocLoader.Change c : changes) {
                if (c.url() == null) continue;
                Term id = new Term("id", c.url());
                TopDocs td = searcher.search(new TermQuery(id), 1);
                boolean exists = td.scoreDocs.length > 0;
                if (c.doc() == null) {            // thanh trang trung / rong -> bo khoi chi muc
                    if (exists) {
                        writer.deleteDocuments(id);
                        deleted++;
                    }
                    continue;
                }
                String sig = signature(c.doc());
                if (exists && sig.equals(stored.document(td.scoreDocs[0].doc, Set.of(SIG)).get(SIG))) {
                    unchanged++;                  // crawler ghi lai nhung noi dung khong doi
                    continue;
                }
                try {
                    writer.updateDocument(id, toLuceneDoc(c.doc()));
                } catch (IllegalArgumentException e) {
                    System.err.println("  [bo qua] " + c.url() + ": " + e.getMessage());
                    continue;
                }
                if (exists) updated++;
                else added++;
            }
            writer.setLiveCommitData(Map.of(SINCE, newSince).entrySet());
            writer.commit();
        }
        return new SyncStats(added, updated, deleted, unchanged);
    }

    /** Moc updated_at da dong bo toi (null neu chi muc lap tu thu muc / chua co). */
    static String since(Path indexDir) throws IOException {
        try (Directory dir = FSDirectory.open(indexDir)) {
            if (!DirectoryReader.indexExists(dir)) return null;
            try (DirectoryReader r = DirectoryReader.open(dir)) {
                return r.getIndexCommit().getUserData().get(SINCE);
            }
        }
    }

    private static IndexWriterConfig config(Analyzer analyzer, Analyzer spellAnalyzer, IndexWriterConfig.OpenMode mode) {
        // field spell dung analyzer rieng (giu dau), cac field con lai dung analyzer chinh
        IndexWriterConfig cfg = new IndexWriterConfig(
                new PerFieldAnalyzerWrapper(analyzer, Map.of(SpellSuggester.FIELD, spellAnalyzer)));
        cfg.setOpenMode(mode);
        cfg.setRAMBufferSizeMB(RAM_BUFFER_MB);
        return cfg;
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
        // sig: chu ky noi dung, de dong bo bo qua van ban crawler ghi lai ma khong doi
        doc.add(new StoredField(SIG, signature(d)));
        return doc;
    }

    /** SHA-1 cua title + intro + content (moi thu duoc dua vao chi muc). */
    static String signature(DocLoader.Doc d) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-1");
            for (String part : new String[]{d.title(), d.intro(), d.content()}) {
                md.update(part.getBytes(StandardCharsets.UTF_8));
                md.update((byte) 0);
            }
            return HexFormat.of().formatHex(md.digest());
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
