const POLL_MS = 2000;
const JOB_KEY = 'crawl-job';
let crawlTimer = null;
let crawlJob = null;

// Nho job de tai lai trang van thay tien do va con nut Dung.
function rememberJob(job) {
  try {
    if (job) sessionStorage.setItem(JOB_KEY, job);
    else sessionStorage.removeItem(JOB_KEY);
  } catch (e) { /* trinh duyet chan storage: chi mat tinh nang nay */ }
}

function followJob(job) {
  crawlJob = job;
  rememberJob(job);
  $('crawl-start').disabled = true;
  $('crawl-stop').disabled = false;
  pollCrawl(job);
}

function showCrawl(s) {
  const of = s.max_pages ? ` / ${number(s.max_pages)}` : '';
  let html = `<div class="progress">Đã lưu ${number(s.saved)}${of} trang</div>`;
  html += `<div class="info">${esc(s.host)} · đã xếp hàng ${number(s.queued)} URL</div>`;
  if (s.done) {
    html += s.saved
      ? `<div class="note">${s.stopped ? 'Đã dừng.' : 'Đã xong.'} Chỉ mục cập nhật sau khoảng 10–20 giây.
         ${!s.stopped && s.answered < s.queued ? `Có ${number(s.queued - s.answered)} URL không lưu được (trang chặn hoặc tải lỗi).` : ''}</div>
         <p><button type="button" id="crawl-try">Tìm thử</button></p>`
      : `<div class="note err">${s.stopped ? 'Đã dừng, chưa lưu được trang nào.' : 'Không tải được trang nào từ link này.'}</div>`;
  }
  html += '<ul class="urls">' + s.urls.map(u => `<li>${linkTo(u)}</li>`).join('') + '</ul>';
  $('crawl-out').innerHTML = html;
  if (s.done) {
    $('crawl-try')?.addEventListener('click', () => tryFind(s));
    endCrawl();
  }
}

function endCrawl() {
  clearTimeout(crawlTimer);
  crawlTimer = null;
  crawlJob = null;
  rememberJob(null);
  $('crawl-start').disabled = false;
  $('crawl-stop').disabled = true;
}

function crawlError(text) {
  $('crawl-out').innerHTML = `<div class="info err">${esc(text)}</div>`;
  endCrawl();
}

async function pollCrawl(job) {
  let r;
  try {
    r = await getJson('/crawl/' + encodeURIComponent(job));
  } catch (e) {
    r = null;
  }
  if (job !== crawlJob) return;  // da co lan crawl khac hoac da ket thuc
  if (r?.status === 404) return crawlError('Máy chủ đã khởi động lại nên mất lần crawl này.');
  if (r?.ok) {
    $('crawl-url').value = r.body.url;
    showCrawl(r.body);
  }
  if (crawlJob === job) crawlTimer = setTimeout(() => pollCrawl(job), POLL_MS);
}

async function tryFind(s) {
  // Lay tieu de dai nhat trong cac trang vua luu: trang chu thuong co tieu de chung chung.
  const pages = await Promise.all(s.urls.slice(0, 10).map(url =>
    getJson('/api/doc?kind=html&url=' + encodeURIComponent(url)).catch(() => null)));
  const titles = pages.map(r => r?.body?.title || '');
  goSearch(titles.reduce((a, b) => (b.length > a.length ? b : a), '') || s.host);
}

$('crawl-form').addEventListener('submit', async e => {
  e.preventDefault();
  const raw = $('crawl-max').value.trim();
  const body = {url: $('crawl-url').value.trim(), max_pages: raw ? Number(raw) : null};
  $('crawl-start').disabled = true;
  $('crawl-out').innerHTML = '<div class="info">Đang gửi…</div>';
  let r;
  try {
    r = await getJson('/crawl', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
  } catch (err) {
    return crawlError('Không kết nối được crawler.');
  }
  if (r.status === 400) return crawlError(`Link hoặc số trang không hợp lệ: ${body.url}`);
  if (!r.ok) return crawlError('Crawler chưa sẵn sàng, thử lại sau ít phút.');
  followJob(r.body.job);
});

$('crawl-stop').addEventListener('click', async () => {
  if (!crawlJob) return;
  const r = await getJson(`/crawl/${encodeURIComponent(crawlJob)}/stop`, {method: 'POST'}).catch(() => null);
  if (r?.ok) showCrawl(r.body);
});

try {
  const saved = sessionStorage.getItem(JOB_KEY);
  if (saved) followJob(saved);
} catch (e) { /* xem rememberJob */ }
