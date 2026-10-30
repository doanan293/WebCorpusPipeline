const PAGE_SIZE = {html: 20, other: 20, picture: 24};
const dataState = {kind: 'html', page: 1, total: 0};
const pictureUrl = url => '/api/picture?url=' + encodeURIComponent(url);

async function loadStats() {
  const r = await getJson('/api/stats').catch(() => null);
  if (!r?.ok) return;
  const s = r.body;
  const fetched = s.pictureFetched == null ? '' : ` (${number(s.pictureFetched)} đã tải)`;
  $('data-stats').textContent =
    `${number(s.html)} trang · ${number(s.other)} tệp · ${number(s.picture)} ảnh${fetched}`;
}

function htmlTable(docs) {
  return `<table class="table"><thead><tr><th>Trang</th><th class="when">Cập nhật</th></tr></thead><tbody>`
    + docs.map(d => `<tr data-url="${esc(d.url)}"><td><div>${esc(d.title || '(không có tiêu đề)')}</div>
        <div class="tag">${esc(d.url)}${d.duplicate_of ? ' · trùng nội dung' : ''}</div></td>
        <td class="when">${when(d.updated_at)}</td></tr>`).join('')
    + '</tbody></table>';
}

function otherTable(docs) {
  return `<table class="table"><thead><tr><th>Tệp</th><th class="small">Loại</th><th class="when">Cập nhật</th></tr></thead><tbody>`
    + docs.map(d => `<tr data-url="${esc(d.url)}"><td>${esc(d.url)}</td><td class="small">${esc(d.format)}</td>
        <td class="when">${when(d.updated_at)}</td></tr>`).join('')
    + '</tbody></table>';
}

function pictureGrid(docs) {
  return '<div class="grid">' + docs.map(d =>
    `<button type="button" class="tile" data-url="${esc(d.url)}" title="${esc(d.url)}">
       <img loading="lazy" alt="" src="${esc(pictureUrl(d.url))}"></button>`).join('') + '</div>';
}

async function loadData() {
  const {kind, page} = dataState;
  const size = PAGE_SIZE[kind];
  document.querySelectorAll('.subtabs button').forEach(b => b.classList.toggle('active', b.dataset.kind === kind));
  $('data-out').innerHTML = '<div class="info">Đang tải…</div>';
  // Tab Anh chi hien anh da tai: anh cua trang vua crawl chua co byte (tai rieng bang `crawler pictures fetch`).
  const fetched = kind === 'picture' ? '&fetched=1' : '';
  const r = await getJson(`/api/docs?kind=${kind}&page=${page}&size=${size}${fetched}`).catch(() => null);
  if (kind !== dataState.kind || page !== dataState.page) return;  // da bam sang tab / trang khac
  if (!r?.ok) {
    $('data-out').innerHTML = '<div class="info err">Không đọc được dữ liệu (lần đầu cần chờ lập chỉ mục).</div>';
    return;
  }
  dataState.total = r.body.total;
  const docs = r.body.docs;
  $('data-out').innerHTML = !docs.length ? '<div class="info">Chưa có dữ liệu.</div>'
    : kind === 'html' ? htmlTable(docs)
    : kind === 'other' ? otherTable(docs)
    : pictureGrid(docs);
  const pages = Math.max(1, Math.ceil(r.body.total / size));
  $('data-page').textContent = `Trang ${number(page)} / ${number(pages)} · ${number(r.body.total)} mục`;
  $('data-prev').disabled = page <= 1;
  $('data-next').disabled = page >= pages;
}

async function openDetail(kind, url) {
  $('detail-out').innerHTML = '<div class="info">Đang tải…</div>';
  $('detail').showModal();
  const r = await getJson(`/api/doc?kind=${kind}&url=${encodeURIComponent(url)}`).catch(() => null);
  if (!r?.ok) {
    $('detail-out').innerHTML = '<div class="info err">Không đọc được bản ghi này.</div>';
    return;
  }
  const d = r.body;
  let html = `<h2>${esc(d.title || d.url)}</h2><div class="info">${linkTo(d.url, 'Mở URL gốc')} · ${when(d.updated_at)}</div>`;
  if (kind === 'picture') {
    html += d.size == null
      ? `<div class="note err">${esc(d.error || 'Ảnh chưa được tải')}</div>`
      : `<img alt="" src="${esc(pictureUrl(d.url))}">`;
    html += `<h3>Trang chứa ảnh</h3><div>${linkTo(d.origin)}</div>`;
  } else {
    if (d.duplicate_of) html += `<h3>Trùng nội dung với</h3><div>${linkTo(d.duplicate_of)}</div>`;
    if (d.origin) html += `<h3>Trang dẫn tới tệp</h3><div>${linkTo(d.origin)}</div>`;
    html += `<h3>Nội dung (trích)</h3><pre>${esc(d.content || '(rỗng)')}</pre>`;
    if (d.intro) html += `<h3>Văn bản các liên kết trỏ tới</h3><pre>${esc(d.intro)}</pre>`;
    if (d.images?.length) {
      html += `<h3>Ảnh của trang (${d.images.length})</h3><div class="thumbs">`
        + d.images.map(u => `<img loading="lazy" alt="" src="${esc(pictureUrl(u))}">`).join('') + '</div>';
    }
    if (d.outlinks?.length) {
      html += `<h3>Liên kết đi (${d.outlinks.length})</h3><ul class="urls">`
        + d.outlinks.map(u => `<li>${linkTo(u)}</li>`).join('') + '</ul>';
    }
  }
  $('detail-out').innerHTML = html;
}

document.querySelector('.subtabs').addEventListener('click', e => {
  const kind = e.target.closest('button')?.dataset.kind;
  if (!kind) return;
  dataState.kind = kind;
  dataState.page = 1;
  loadData();
});
$('data-prev').addEventListener('click', () => { dataState.page--; loadData(); });
$('data-next').addEventListener('click', () => { dataState.page++; loadData(); });
$('data-out').addEventListener('click', e => {
  const el = e.target.closest('[data-url]');
  if (el) openDetail(dataState.kind, el.dataset.url);
});
// Su kien error khong noi bot nen bat o pha capture: anh loi thanh o xam.
$('data-out').addEventListener('error', e => {
  if (e.target.tagName === 'IMG') e.target.outerHTML = '<span class="none">không hiện được</span>';
}, true);
$('detail-out').addEventListener('error', e => {
  if (e.target.tagName === 'IMG') e.target.remove();
}, true);
$('detail-close').addEventListener('click', () => $('detail').close());
