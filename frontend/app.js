const TABS = ['crawl', 'data', 'search'];

function route() {
  const [name, query] = location.hash.slice(1).split('?');
  const tab = TABS.includes(name) ? name : 'crawl';
  for (const t of TABS) $('tab-' + t).hidden = t !== tab;
  document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('active', a.dataset.tab === tab));
  document.title = 'WebCorpus Demo';
  if (tab === 'data') {
    loadStats();
    loadFeatured();
    loadData();
  } else if (tab === 'search') {
    const q = new URLSearchParams(query || '').get('q');
    if (q) search(q);
    else { $('q').value = ''; $('out').innerHTML = ''; $('q').focus(); }
  }
}

window.addEventListener('hashchange', route);
route();

getJson('/api/info').then(r => {
  if (r.ok) $('sub').textContent = `Crawler · MongoDB · Lucene · ${number(r.body.docs)} văn bản trong chỉ mục`;
}).catch(() => {});
