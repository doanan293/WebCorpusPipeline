const PAGE_SIZE = {html: 20, other: 20, picture: 24};
const dataState = {kind: 'html', page: 1, total: 0, allPictures: false};
const pictureUrl = url => '/api/picture?url=' + encodeURIComponent(url);
// Trang va tep tieu bieu cho buoi demo; muc nao khong con trong corpus thi tu an.
const FEATURED_URL = 'https://soict.hust.edu.vn/summer-school';
const FEATURED_FILES = [
  'https://soict.hust.edu.vn/wp-content/uploads/Meta-Learning-for-Adaptive-Edge-AI.pdf',
  'https://hust.edu.vn/uploads/sys/news/2017/10/tai-lieu-mo-ta-vpdt.22699.10966.docx',
  'https://clc.hust.edu.vn/xmedia/2020/10/cla2tkb-tatc-k6518102020-.xlsx',
  'https://hust.edu.vn/uploads/sys/news/2024_05/huong-dan-su-dung-hust-care-2024-gui-cb915693.pptx',
];
// MongoDB khong ghi tep nao qua LlamaParse; so trang nay doi chieu tu cache data/enrichment/cache.
const FILE_NOTES = {
  'https://soict.hust.edu.vn/wp-content/uploads/Meta-Learning-for-Adaptive-Edge-AI.pdf': 'LlamaParse · 51 trang',
};
let detailRequest = 0;

const fileName = url => {
  const last = String(url).split(/[?#]/)[0].split('/').filter(Boolean).pop() || url;
  try { return decodeURIComponent(last); } catch (e) { return last; }
};

function fileButtons(files) {
  return '<div class="files">' + files.map(f =>
    `<button type="button" class="file" data-kind="other" data-url="${esc(f.url)}">
       <span class="fmt">${esc(f.format)}</span><span class="name">${esc(fileName(f.url))}</span>
       ${FILE_NOTES[f.url] ? `<span class="note">${esc(FILE_NOTES[f.url])}</span>` : ''}
       <span class="size">${number(f.contentLength)} ký tự</span></button>`).join('') + '</div>';
}

async function loadFeatured() {
  if ($('data-featured').innerHTML) return;
  const read = (kind, url) => getJson(`/api/doc?kind=${kind}&url=${encodeURIComponent(url)}`)
    .then(r => (r.ok ? r.body : null)).catch(() => null);
  const [d, ...files] = await Promise.all([
    read('html', FEATURED_URL), ...FEATURED_FILES.map(url => read('other', url)),
  ]);
  let html = '';
  if (d) {
    html += `<div class="label">Trang tiêu biểu</div>
      <h2>${esc(d.title)}</h2>
      <div class="meta">${number(d.contentLength)} ký tự · ${number(d.images?.length ?? 0)} ảnh · ${number(d.files.length)} tệp đính kèm</div>
      <button type="button" class="open" data-kind="html" data-url="${esc(d.url)}">Xem trang này</button>`;
  }
  const found = files.filter(Boolean);
  if (found.length) html += `<div class="sub-label">Tệp tiêu biểu theo định dạng</div>${fileButtons(found)}`;
  if (html) $('data-featured').innerHTML = `<div class="featured">${html}</div>`;
}

async function loadStats() {
  const r = await getJson('/api/stats').catch(() => null);
  if (!r?.ok) return;
  const s = r.body;
  $('data-stats').textContent =
    `${number(s.html)} trang · ${number(s.other)} tệp · ${number(s.picture)} ảnh (${number(s.pictureFetched)} đã tải)`;
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
  return '<div class="grid">' + docs.map(d => {
    const inner = d.size == null
      ? `<span class="none">${esc(d.error || 'chưa tải')}<br>${esc(d.url)}</span>`
      : `<img loading="lazy" alt="" src="${esc(pictureUrl(d.url))}">`;
    return `<button type="button" class="tile" data-url="${esc(d.url)}" title="${esc(d.url)}">${inner}</button>`;
  }).join('') + '</div>';
}

async function loadData() {
  const {kind, page} = dataState;
  const size = PAGE_SIZE[kind];
  document.querySelectorAll('.subtabs button').forEach(b => b.classList.toggle('active', b.dataset.kind === kind));
  $('data-out').innerHTML = '<div class="info">Đang tải…</div>';
  // Tab Anh chi hien anh da tai: anh cua trang vua crawl chua co byte (tai rieng bang `crawler pictures fetch`).
  $('data-all').hidden = kind !== 'picture';
  const fetched = kind === 'picture' && !dataState.allPictures ? '&fetched=1' : '';
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
  const request = ++detailRequest;
  $('detail-out').innerHTML = '<div class="info">Đang tải…</div>';
  if (!$('detail').open) $('detail').showModal();
  $('detail').scrollTop = 0;
  const r = await getJson(`/api/doc?kind=${kind}&url=${encodeURIComponent(url)}`).catch(() => null);
  if (request !== detailRequest) return;  // da bam sang muc khac
  if (!r?.ok) {
    $('detail-out').innerHTML = `<div class="info err">Không đọc được bản ghi này.</div><div>${linkTo(url)}</div>`;
    return;
  }
  const d = r.body;
  const title = d.title || (kind === 'other' ? fileName(d.url) : d.url);
  let html = `<h2>${esc(title)}</h2><div class="info">${linkTo(d.url, 'Mở URL gốc')} · ${when(d.updated_at)}</div>`;
  if (kind === 'picture') {
    html += d.size == null
      ? `<div class="note err">${esc(d.error || 'Ảnh chưa được tải')}</div>`
      : `<img alt="" src="${esc(pictureUrl(d.url))}">`;
    html += `<h3>Trang chứa ảnh</h3><div>${linkTo(d.origin)}</div>`;
  } else {
    if (d.duplicate_of) html += `<h3>Trùng nội dung với</h3><div>${linkTo(d.duplicate_of)}</div>`;
    if (d.origin) {
      html += `<h3>Trang dẫn tới tệp này</h3><button type="button" class="file" data-kind="html" data-url="${esc(d.origin)}">
        <span class="fmt">trang</span><span class="name">${esc(d.origin)}</span></button>`;
    }
    if (FILE_NOTES[d.url]) html += `<h3>Cách đọc</h3><div>${esc(FILE_NOTES[d.url])} (OCR các trang có hình)</div>`;
    html += `<h3>Toàn bộ nội dung (${number(d.contentLength ?? 0)} ký tự)</h3><pre>${esc(d.content || '(rỗng)')}</pre>`;
    if (d.files?.length) html += `<h3>Tệp đính kèm (${d.files.length})</h3>${fileButtons(d.files)}`;
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
$('data-all-box').addEventListener('change', e => {
  dataState.allPictures = e.target.checked;
  dataState.page = 1;
  loadData();
});
$('data-prev').addEventListener('click', () => { dataState.page--; loadData(); });
$('data-next').addEventListener('click', () => { dataState.page++; loadData(); });
$('data-out').addEventListener('click', e => {
  const el = e.target.closest('[data-url]');
  if (el) openDetail(dataState.kind, el.dataset.url);
});
// Nut mang san loai ban ghi: chuyen qua lai giua trang va tep cua no.
for (const box of [$('data-featured'), $('detail-out')]) {
  box.addEventListener('click', e => {
    const el = e.target.closest('[data-kind][data-url]');
    if (el) openDetail(el.dataset.kind, el.dataset.url);
  });
}
// Su kien error khong noi bot nen bat o pha capture: anh loi thanh o xam.
$('data-out').addEventListener('error', e => {
  if (e.target.tagName !== 'IMG') return;
  const url = e.target.closest('[data-url]')?.dataset.url;
  e.target.outerHTML = `<span class="none">không hiện được<br>${esc(url)}</span>`;
}, true);
$('detail-out').addEventListener('error', e => {
  if (e.target.tagName === 'IMG') e.target.remove();
}, true);
$('detail-close').addEventListener('click', () => $('detail').close());
