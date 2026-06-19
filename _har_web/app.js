/* HAR Dashboard — Alpine.js State + Chart.js */

function appState() {
  return {
    // Navigation
    page: 'home',
    dayDate: null,
    range: '7d',
    selectedWikiSlug: null,
    loading: false,
    error: null,

    // Data stores
    data: { home: null },
    meta: {},
    timeStats: null,
    wikiData: null,
    wikiDetail: null,
    needsCategorization: [],
    calendarDays: [],
    calendarMonth: new Date().getMonth() + 1,
    calendarYear: new Date().getFullYear(),
    dayDetail: null,
    plansData: null,
    // Plan tomorrow state
    showPlanInput: false,
    planInputText: '',
    savingPlan: false,
    planSaved: false,
    planError: null,
    tomorrowDate: '',

    // Computed: activities with computed_stats from timeStats
    get statsActivities() {
      if (!this.timeStats || !this.timeStats.category_breakdown) return [];
      const results = [];
      for (const cat of this.timeStats.category_breakdown) {
        if (!cat.activities) continue;
        for (const act of cat.activities) {
          if (act.computed_stats && act.computed_stats.length > 0) {
            results.push({
              activity: act.name,
              slug: act.name.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, ''),
              category: cat.category,
              categoryClass: cat.category.replace(/[^a-zA-Z]/g, '').toLowerCase(),
              stats: act.computed_stats,
            });
          }
        }
      }
      return results;
    },

    // Chart instances
    _charts: {},
    _busy: false,

    // ── Init ──
    async init() {
      this.calendarMonth = new Date().getMonth() + 1;
      this.calendarYear = new Date().getFullYear();
      this._updateTomorrow();
      await this.fetchMeta();
      await this.goTo(null);
    },

    // ── Load a page: tab click, init, back/forward all go through here ──
    async goTo(pageName) {
      if (this._busy) return;
      this._busy = true;
      this.destroyCharts();
      this.loading = true;
      this.error = null;

      // Determine target page
      const target = pageName || window.location.hash.replace(/^#/, '') || 'home';

      try {
        if (target === 'home') {
          this.page = 'home';
          await this.fetchHome();
        } else if (target === 'time-stats') {
          this.page = 'time-stats';
          await this.fetchTimeStats();
        } else if (target === 'wikis') {
          this.page = 'wikis';
          this.selectedWikiSlug = null;
          this.wikiDetail = null;
          await this.fetchWikis();
        } else if (target === 'calendar') {
          this.page = 'calendar';
          await Promise.all([this.fetchCalendar(), this.fetchPlans()]);
        } else if (target.startsWith('wikis/')) {
          // Wiki detail page via URL hash
          const slug = target.replace('wikis/', '');
          this.page = 'wikis';
          this.selectedWikiSlug = slug;
          this.wikiDetail = null;
          await this.fetchWikis();
          // Find and load the wiki detail
          if (this.wikiData && this.wikiData.by_category) {
            for (const cat of this.wikiData.by_category) {
              const found = cat.activities.find(a => a.slug === slug);
              if (found) {
                await this.drillWiki(slug, found.name);
                break;
              }
            }
          }
          if (!this.wikiDetail) {
            // Try fetching directly
            await this.drillWiki(slug, slug.replace(/-/g, ' '));
          }
        } else if (target.startsWith('day/')) {
          this.dayDate = target.replace('day/', '');
          this.page = 'day';
          await this.fetchDay(this.dayDate);
        } else {
          this.page = 'home';
          await this.fetchHome();
        }
      } catch (e) {
        this.error = e.message || 'Something went wrong';
        console.error('Navigation error:', e);
      } finally {
        this.loading = false;
        this._busy = false;
      }
    },

    // ── API with timeout safety ──
    async api(path, timeoutMs = 15000) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        const res = await fetch(path, { signal: controller.signal });
        clearTimeout(timeoutId);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
      } catch (e) {
        console.error(`API error: ${path}`, e);
        return null;
      }
    },

    async fetchMeta() {
      this.meta = await this.api('/api/meta') || {};
    },

    // ── Ensure Chart.js is loaded before rendering charts ──
    async _ensureChartJs() {
      let waited = 0;
      while (typeof Chart === 'undefined' && waited < 100) {
        await new Promise(r => setTimeout(r, 100));
        waited++;
      }
    },

    async fetchHome() {
      const home = await this.api('/api/home');
      if (home) {
        this.data = this.data || {};
        this.data.home = home;
      }
      // Check for uncategorized activities (entries where category is uncategorized/unknown)
      if (this.data.home && this.data.home.category_breakdown) {
        const uncat = this.data.home.category_breakdown.find(c => c.category === 'uncategorized');
        this.needsCategorization = uncat && uncat.activities ? uncat.activities.map(a => a.name) : [];
      }
      await this._ensureChartJs();
      this.$nextTick(() => {
        try { this.renderHomeChart(); } catch(e) { console.error('Home chart error:', e); }
      });
    },

    async fetchTimeStats() {
      this.destroyCharts();
      this.timeStats = await this.api(`/api/time-stats?range=${this.range}`);
      await this._ensureChartJs();
      this.$nextTick(() => {
        try { this.renderTimeChart(); } catch(e) { console.error('Time chart error:', e); }
      });
    },

    async fetchWikis() {
      this.wikiData = await this.api('/api/wikis');
    },

    async drillWiki(slug, activityName) {
      this.selectedWikiSlug = slug;
      this.wikiDetail = null;
      const seq = (this._wikiSeq = (this._wikiSeq || 0) + 1);
      const result = await this.api(`/api/wikis/${slug}`);
      if (this._wikiSeq !== seq) return; // stale response, newer drill in flight
      if (result) {
        this.wikiDetail = result;
      } else {
        this.wikiDetail = null;
        this.error = `Wiki not found: ${activityName}`;
        console.error(`Wiki not found: ${slug}`);
      }
    },

    async fetchCalendar() {
      const allMonths = await this.api('/api/calendar');
      const current = Array.isArray(allMonths)
        ? allMonths.find(m => m.year === this.calendarYear && m.month === this.calendarMonth)
        : null;
      this.buildCalendarGrid(current);
    },

    async fetchPlans() {
      this.plansData = await this.api('/api/plans') || {};
    },

    async fetchDay(day) {
      this.dayDetail = await this.api(`/api/day/${day}`);
      // Sort actuals by time (chronological order)
      if (this.dayDetail && this.dayDetail.actuals) {
        this.dayDetail.actuals.sort((a, b) => {
          const tA = a.time || '00:00';
          const tB = b.time || '00:00';
          return tA.localeCompare(tB);
        });
      }
    },

    // ── Calendar Grid Builder ──
    buildCalendarGrid(monthData) {
      const days = [];
      const year = this.calendarYear;
      const month = this.calendarMonth;
      const firstDay = new Date(year, month - 1, 1).getDay();
      const daysInMonth = new Date(year, month, 0).getDate();
      for (let i = 0; i < firstDay; i++) {
        days.push({ num: '', date: null, state: null });
      }
      for (let d = 1; d <= daysInMonth; d++) {
        const ds = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const dayInfo = monthData && monthData.days ? monthData.days[ds] : null;
        days.push({
          num: d,
          date: ds,
          state: dayInfo ? dayInfo.state : 'neutral',
          notes: dayInfo ? dayInfo.notes_preview : '',
        });
      }
      this.calendarDays = days;
    },

    // ── Plan Tomorrow ──
    _updateTomorrow() {
      const d = new Date();
      d.setDate(d.getDate() + 1);
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      this.tomorrowDate = `${y}-${m}-${day}`;
    },

    openPlanInput() {
      this.showPlanInput = true;
      this.planInputText = '';
      this.planSaved = false;
      this.planError = null;
      this.savingPlan = false;
    },

    cancelPlan() {
      this.showPlanInput = false;
      this.planInputText = '';
      this.planSaved = false;
      this.planError = null;
    },

    async savePlan() {
      if (!this.planInputText.trim()) {
        this.planError = 'Write something before saving!';
        return;
      }
      this.savingPlan = true;
      this.planError = null;
      this.planSaved = false;
      try {
        const res = await fetch('/api/save-plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            date: this.tomorrowDate,
            plan: this.planInputText.trim(),
          }),
        });
        const data = await res.json();
        if (data.status === 'ok') {
          this.planSaved = true;
          this.showPlanInput = false;
          this.planInputText = '';
          // Refresh calendar to show the new plan
          await this.fetchCalendar();
        } else {
          this.planError = data.error || 'Failed to save plan';
        }
      } catch (e) {
        this.planError = e.message || 'Network error';
      } finally {
        this.savingPlan = false;
      }
    },

    // ── Toggle activity — navigate to most recent day with this activity ──
    async toggleActivity(cat, actName) {
      try {
        const res = await fetch('/api/calendar');
        const allMonths = await res.json();
        if (!Array.isArray(allMonths)) return;
        const allDays = [];
        for (const md of allMonths) {
          if (md.days) {
            for (const ds of Object.keys(md.days)) {
              const dayInfo = md.days[ds];
              if (dayInfo.state !== 'neutral' && dayInfo.state !== 'green') {
                allDays.push(ds);
              }
            }
          }
        }
        allDays.sort().reverse();
        for (const ds of allDays) {
          const dayData = await this.api(`/api/day/${ds}`);
          if (dayData && dayData.actuals) {
            const match = dayData.actuals.find(a => a.activity === actName);
            if (match) {
              this.goTo('day/' + ds);
              return;
            }
          }
        }
      } catch(e) {
        console.error('toggleActivity error:', e);
      }
    },

    async changeMonth(delta) {
      this.calendarMonth += delta;
      if (this.calendarMonth < 1) { this.calendarMonth = 12; this.calendarYear--; }
      if (this.calendarMonth > 12) { this.calendarMonth = 1; this.calendarYear++; }
      this.loading = true;
      await this.fetchCalendar();
      this.loading = false;
    },

    // ── Generate color shades ──
    _hexToRgb(hex) {
      const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
      return result ? { r: parseInt(result[1], 16), g: parseInt(result[2], 16), b: parseInt(result[3], 16) } : { r: 100, g: 100, b: 100 };
    },

    _shadeColor(hex, factor) {
      const { r, g, b } = this._hexToRgb(hex);
      return `rgba(${Math.round(r * factor)}, ${Math.round(g * factor)}, ${Math.round(b * factor)}, 1)`;
    },

    // ── Chart Renderers ──
    getCanvas(id) {
      const el = document.getElementById(id);
      if (!el) return null;
      return el.getContext('2d');
    },

    destroyCharts() {
      Object.values(this._charts).forEach(c => { try { c.destroy(); } catch(e) {} });
      this._charts = {};
    },

    renderHomeChart() {
      const canvas = this.getCanvas('homeChart');
      if (!canvas || !this.data.home || !this.data.home.category_breakdown) return;
      const breakdown = this.data.home.category_breakdown;
      if (!breakdown.length) return;

      const labels = breakdown.map(c => c.category.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase()));
      const values = breakdown.map(c => c.total_minutes);
      const colors = breakdown.map(c => c.color);

      this._charts.homeChart = new Chart(canvas, {
        type: 'doughnut',
        data: {
          labels,
          datasets: [{
            data: values,
            backgroundColor: colors,
            borderWidth: 0,
            hoverOffset: 8,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          cutout: '65%',
          plugins: {
            legend: {
              position: 'bottom',
              labels: {
                color: '#1A1A1A',
                font: { size: 12, weight: '500' },
                boxWidth: 14,
                padding: 16,
                usePointStyle: true,
              },
            },
            tooltip: {
              callbacks: {
                label: (item) => {
                  const total = item.dataset.data.reduce((a, b) => a + b, 0);
                  const pct = total > 0 ? Math.round((item.raw / total) * 100) : 0;
                  return `${item.label}: ${item.raw} min (${pct}%)`;
                }
              }
            }
          }
        }
      });
    },

    renderTimeChart() {
      const canvas = this.getCanvas('timeChart');
      if (!canvas || !this.timeStats || !this.timeStats.category_breakdown) return;
      const breakdown = this.timeStats.category_breakdown;
      if (!breakdown.length) return;

      // Build stacked datasets: each activity within a category gets a shade of that category's color
      const datasets = [];
      const categoryLabels = breakdown.map(c =>
        c.category.replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
      );

      // Collect all unique activity names across all categories
      const allActivities = {};
      breakdown.forEach(cat => {
        if (cat.activities) {
          cat.activities.forEach(act => {
            allActivities[act.name] = true;
          });
        }
      });
      const activityNames = Object.keys(allActivities);

      // Build a map: category -> array of activities with their shade factors
      breakdown.forEach(cat => {
        const acts = cat.activities || [];
        const n = acts.length || 1;
        acts.forEach((act, i) => {
          // Shade factor from 0.4 (light) to 0.9 (dark), spread evenly
          const factor = 0.4 + (i / Math.max(n - 1, 1)) * 0.5;
          datasets.push({
            label: act.name,
            data: categoryLabels.map((_, ci) => ci === breakdown.indexOf(cat) ? act.total_minutes : 0),
            backgroundColor: this._shadeColor(cat.color, factor),
            borderRadius: 2,
            borderSkipped: false,
          });
        });
      });

      this._charts.timeChart = new Chart(canvas, {
        type: 'bar',
        data: {
          labels: categoryLabels,
          datasets,
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: {
              display: true,
              position: 'bottom',
              labels: {
                color: '#1A1A1A',
                font: { size: 11 },
                boxWidth: 14,
                padding: 12,
                usePointStyle: true,
              },
            },
            tooltip: {
              callbacks: {
                title: (items) => {
                  if (items.length) return items[0].dataset.label;
                  return '';
                },
                label: (item) => {
                  return `${item.raw} min`;
                }
              }
            }
          },
          scales: {
            x: {
              stacked: true,
              beginAtZero: true,
              ticks: { color: '#6B6560' },
              grid: { color: '#E5DFD5' },
            },
            y: {
              stacked: true,
              ticks: { color: '#1A1A1A', font: { weight: '500', size: 13 } },
              grid: { display: false },
              barThickness: 28,
            },
          }
        }
      });
    },

  };
}
