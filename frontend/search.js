function hitHtml(h, i, pagerank, extra) {
  // van ban tu MongoDB: id la url goc, source la "mongo:html" / "mongo:other"; van ban tu thu muc: id la ten tep
  const link = isUrl(h.id) ? h.id : isUrl(h.source) ? h.source : null;
  const title = h.title || h.id;
  const head = link
    ? `<a class="title" href="${esc(link)}" target="_blank" rel="noopener">${esc(title)}</a>`
    : `<span class="title">${esc(title)}</span>`;
  const meta = extra
    ? `tf-idf ${h.tfidf.toFixed(3)}`
    : `#${i + 1} · điểm ${h.score.toFixed(3)} · tf-idf ${h.tfidf.toFixed(3)}` + (pagerank ? ` · PageRank ${h.pagerank.toFixed(2)}` : '');
  const where = link ? link + (h.source.startsWith('mongo:other') ? '  · tệp' : '') : h.source;
  return `<div class="hit"><div class="url">${esc(where)}</div>${head}
    <div class="snip">${h.snippet}</div><div class="meta">${meta}</div></div>`;
}

// Loai ket qua: trang/tep (pages) hoac anh (pictures); nam tren URL cung truy van.
let searchType = 'pages';

function setSearchType(type) {
  searchType = type === 'pictures' ? 'pictures' : 'pages';
  document.querySelectorAll('#search-type button')
    .forEach(b => b.classList.toggle('active', b.dataset.type === searchType));
}

// Anh tim theo chu di kem (ten tep, tieu de va dau noi dung trang chua anh); bam anh de xem chi tiet.
async function searchPictures(q) {
  const size = {10: 24, 20: 48, 50: 96}[$('limit').value] ?? 24;
  let d;
  try {
    const r = await fetch('/api/pictures?q=' + encodeURIComponent(q) + '&limit=' + size);
    d = await r.json();
  } catch (e) {
    $('out').innerHTML = '<div class="info err">Không kết nối được máy chủ tìm kiếm (lần đầu cần chờ lập chỉ mục).</div>';
    return;
  }
  if (d.error) {
    $('out').innerHTML = `<div class="info err">${esc(d.error)}</div>`;
    return;
  }
  let html = `<div class="info">Khoảng ${number(d.total)} ảnh khớp (${d.ms} ms) · tìm theo tên tệp, tiêu đề và nội dung trang chứa ảnh</div>`;
  if (!d.hits.length) html += '<div class="info">Không có ảnh nào.</div>';
  html += '<div class="grid">' + d.hits.map(h => `<figure class="pic">
      <button type="button" class="tile" data-url="${esc(h.url)}" title="${esc(h.url)}">
        <img loading="lazy" alt="${esc(h.name)}" src="${esc(pictureUrl(h.url))}"></button>
      <figcaption>${linkTo(h.page, h.title || h.page)}</figcaption></figure>`).join('') + '</div>';
  html += `<details><summary>Truy vấn Lucene</summary><p><code>${esc(d.parsed)}</code></p></details>`;
  $('out').innerHTML = html;
}

async function search(q) {
  q = q.trim();
  if (!q) return;
  $('q').value = q;
  document.title = q + ' - Tìm kiếm';
  if (searchType === 'pictures') {
    $('out').innerHTML = '<div class="info">Đang tìm…</div>';
    return searchPictures(q);
  }
  $('out').innerHTML = '<div class="info">Đang tìm…</div>';
  let d;
  try {
    const r = await fetch('/api/search?q=' + encodeURIComponent(q) + '&limit=' + $('limit').value);
    d = await r.json();
  } catch (e) {
    $('out').innerHTML = '<div class="info err">Không kết nối được máy chủ tìm kiếm (lần đầu cần chờ lập chỉ mục).</div>';
    return;
  }
  let html = '';
  if (d.error) {
    $('out').innerHTML = `<div class="info err">${esc(d.error)}</div>`;
    return;
  }
  html += `<div class="info">Khoảng ${d.total.toLocaleString('vi-VN')} kết quả (${d.ms} ms)`
        + (d.hidden ? ` · đã ẩn ${d.hidden} kết quả gần trùng lặp` : '') + '</div>';
  if (d.suggestion) {
    html += `<div class="note">Có phải bạn muốn tìm: <a data-q="${esc(d.suggestion.text)}">${esc(d.suggestion.text)}</a>
             <span class="info">(${d.suggestion.hits} văn bản, so với ${d.suggestion.originalHits})</span></div>`;
  }
  if (d.expansions.length) {
    html += `<div class="note">Đã mở rộng truy vấn: + ${d.expansions.map(esc).join(' | ')}</div>`;
  }
  if (!d.hits.length) html += '<div class="info">Không có kết quả.</div>';
  html += d.hits.map((h, i) => hitHtml(h, i, d.pagerank, false)).join('');
  if (d.extra.length) {
    html += `<details><summary>${d.extra.length} văn bản không khớp cụm chính xác nhưng có từ khoá riêng lẻ</summary>`
          + d.extra.map((h, i) => hitHtml(h, i, false, true)).join('') + '</details>';
  }
  html += `<details><summary>Truy vấn Lucene</summary><p><code>${esc(d.parsed)}</code></p></details>`;
  $('out').innerHTML = html;
}

// Truy van nam tren URL (#search?q=...) de Back/Forward van dung.
function goSearch(q) {
  const target = '#search?q=' + encodeURIComponent(q.trim()) + (searchType === 'pictures' ? '&type=pictures' : '');
  if (location.hash === target) search(q);
  else location.hash = target;
}

$('f').addEventListener('submit', e => { e.preventDefault(); goSearch($('q').value); });
$('out').addEventListener('click', e => {
  const a = e.target.closest('a[data-q]');
  if (a) { e.preventDefault(); goSearch(a.dataset.q); return; }
  const tile = e.target.closest('.tile[data-url]');
  if (tile) openDetail('picture', tile.dataset.url);
});
$('search-type').addEventListener('click', e => {
  const type = e.target.closest('button')?.dataset.type;
  if (!type || type === searchType) return;
  setSearchType(type);
  if ($('q').value.trim()) goSearch($('q').value);
});
// Anh loi (vd. dinh dang trinh duyet khong doc duoc) thanh o xam.
$('out').addEventListener('error', e => {
  if (e.target.tagName !== 'IMG') return;
  e.target.outerHTML = '<span class="none">không hiện được</span>';
}, true);
