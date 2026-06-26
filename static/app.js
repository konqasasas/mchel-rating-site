(() => {
  const byId = (id) => document.getElementById(id);

  function setupLeaderboardFilters() {
    const search = byId('player-search');
    const tier = byId('tier-filter');
    const clear = byId('clear-filters');
    const count = byId('visible-count');
    const rows = [...document.querySelectorAll('[data-player-row]')];
    if (!search || !tier || !clear || !count || rows.length === 0) return;

    const update = () => {
      const query = search.value.trim().toLocaleLowerCase('ja-JP');
      const selectedTier = tier.value;
      let visible = 0;
      for (const row of rows) {
        const matchesName = !query || (row.dataset.name || '').includes(query);
        const matchesTier = !selectedTier || row.dataset.tier === selectedTier;
        const show = matchesName && matchesTier;
        row.hidden = !show;
        if (show) visible += 1;
      }
      count.textContent = `${visible} 人表示中`;
    };

    search.addEventListener('input', update);
    tier.addEventListener('change', update);
    clear.addEventListener('click', () => {
      search.value = '';
      tier.value = '';
      update();
      search.focus();
    });
  }

  function setupCourseFilters() {
    const search = byId('course-search');
    const category = byId('course-category-filter');
    const clear = byId('clear-course-filters');
    const count = byId('course-visible-count');
    const rows = [...document.querySelectorAll('[data-course-row]')];
    if (!search || !category || !clear || !count || rows.length === 0) return;

    const update = () => {
      const query = search.value.trim().toLocaleLowerCase('ja-JP');
      const selectedCategory = category.value;
      let visible = 0;
      for (const row of rows) {
        const matchesName = !query || (row.dataset.name || '').includes(query);
        const matchesCategory = !selectedCategory || row.dataset.category === selectedCategory;
        const show = matchesName && matchesCategory;
        row.hidden = !show;
        if (show) visible += 1;
      }
      count.textContent = `${visible} コース表示中`;
    };

    search.addEventListener('input', update);
    category.addEventListener('change', update);
    clear.addEventListener('click', () => {
      search.value = '';
      category.value = '';
      update();
      search.focus();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    setupLeaderboardFilters();
    setupCourseFilters();
  });
})();
