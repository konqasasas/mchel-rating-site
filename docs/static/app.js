(() => {
  const search = document.getElementById('player-search');
  const tier = document.getElementById('tier-filter');
  const clear = document.getElementById('clear-filters');
  const count = document.getElementById('visible-count');
  const rows = Array.from(document.querySelectorAll('[data-player-row]'));
  if (!search || !tier || !rows.length) return;

  function applyFilters() {
    const term = search.value.trim().toLocaleLowerCase('ja');
    const selectedTier = tier.value;
    let visible = 0;
    rows.forEach((row) => {
      const name = row.dataset.name || '';
      const matchesName = !term || name.includes(term);
      const matchesTier = !selectedTier || row.dataset.tier === selectedTier;
      const show = matchesName && matchesTier;
      row.hidden = !show;
      if (show) visible += 1;
    });
    if (count) count.textContent = `${visible} 人表示中`;
  }

  search.addEventListener('input', applyFilters);
  tier.addEventListener('change', applyFilters);
  clear?.addEventListener('click', () => {
    search.value = '';
    tier.value = '';
    applyFilters();
    search.focus();
  });
})();
