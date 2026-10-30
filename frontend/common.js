const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const isUrl = s => /^https?:\/\//i.test(String(s ?? ''));
// Du lieu lay tu web: chi tao link khi la http(s), khong thi hien chu.
const linkTo = (url, text) => isUrl(url)
  ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(text ?? url)}</a>`
  : esc(text ?? url);
const when = s => {
  const d = new Date(s);
  return isNaN(d) ? esc(s) : d.toLocaleString('vi-VN');
};
const number = n => Number(n).toLocaleString('vi-VN');

async function getJson(url, options) {
  const r = await fetch(url, options);
  let body = null;
  try { body = await r.json(); } catch (e) { /* 502 cua nginx khong phai JSON */ }
  return {status: r.status, ok: r.ok, body};
}
