(() => {
  'use strict';

  const HIDDEN_REASON_CODES = new Set(['VARDIYA_SONU', 'BELIRLENMEDI']);
  const state = {loaded: false, loading: false, reasons: [], daily: [], selected: ''};

  const $ = id => document.getElementById(id);

  function localISO(date) {
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function daysAgo(count) {
    const date = new Date();
    date.setDate(date.getDate() - count);
    return localISO(date);
  }

  function formatDate(value) {
    if (!value) return '—';
    const [year, month, day] = value.split('-');
    return `${day}.${month}.${year}`;
  }

  function formatDuration(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    if (seconds < 60) return `${seconds} sn`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (!hours) return `${minutes} dk`;
    return minutes ? `${hours} sa ${minutes} dk` : `${hours} sa`;
  }

  function formatPercent(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `%${number.toFixed(1).replace('.', ',')}` : '—';
  }

  function setText(id, value) {
    const node = $(id);
    if (node) node.textContent = value;
  }

  function visibleReasons(items) {
    return (items || []).filter(item => !HIDDEN_REASON_CODES.has(item.reason_code));
  }

  function summarizeReasons(reasons) {
    const result = {total_sec: 0, count: 0, planned_sec: 0, unplanned_sec: 0};
    reasons.forEach(reason => {
      const duration = Number(reason.total_sec) || 0;
      result.total_sec += duration;
      result.count += Number(reason.count) || 0;
      if (reason.category === 'planned') result.planned_sec += duration;
      else result.unplanned_sec += duration;
    });
    return result;
  }

  function selectedSummary() {
    if (!state.selected) {
      const summary = summarizeReasons(state.reasons);
      return {
        reason_name: 'Tüm duruş sebepleri',
        color_hex: '#ef4444',
        total_sec: summary.total_sec,
        count: summary.count,
        avg_sec: summary.count ? Math.round(summary.total_sec / summary.count) : 0,
      };
    }
    return state.reasons.find(reason => reason.reason_code === state.selected) || {
      reason_name: 'Seçili sebep', color_hex: '#ef4444', total_sec: 0, count: 0, avg_sec: 0,
    };
  }

  function durationForDay(day) {
    if (state.selected) return Number((day.reasons || {})[state.selected]) || 0;
    return state.reasons.reduce(
      (total, reason) => total + (Number((day.reasons || {})[reason.reason_code]) || 0),
      0,
    );
  }

  function renderDaily() {
    const container = $('dtDailyBars');
    if (!container) return;
    container.replaceChildren();
    const selected = selectedSummary();
    setText('dtDailyTitle', `Günlük Duruş Dağılımı · ${selected.reason_name}`);

    const rows = state.daily
      .map(day => ({date: day.date, duration: durationForDay(day)}))
      .filter(day => day.duration > 0);
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = 'Seçilen kapsamda duruş kaydı yok.';
      container.appendChild(empty);
      return;
    }
    const maximum = Math.max(...rows.map(row => row.duration), 1);
    rows.forEach(row => {
      const line = document.createElement('div');
      line.className = 'daily-row';

      const date = document.createElement('div');
      date.className = 'daily-date';
      date.textContent = formatDate(row.date).slice(0, 5);

      const track = document.createElement('div');
      track.className = 'daily-track';
      track.title = `${formatDate(row.date)} · ${formatDuration(row.duration)}`;
      const fill = document.createElement('div');
      fill.className = 'daily-fill';
      fill.style.width = `${(row.duration / maximum) * 100}%`;
      fill.style.background = selected.color_hex || '#ef4444';
      track.appendChild(fill);

      const value = document.createElement('div');
      value.className = 'daily-value';
      value.textContent = formatDuration(row.duration);
      line.append(date, track, value);
      container.appendChild(line);
    });
  }

  function renderSelectedReason() {
    const selected = selectedSummary();
    const total = summarizeReasons(state.reasons).total_sec;
    setText('dtSelectedDuration', formatDuration(selected.total_sec));
    setText('dtSelectedCount', (Number(selected.count) || 0).toLocaleString('tr-TR'));
    setText('dtSelectedAverage', formatDuration(selected.avg_sec));
    setText('dtSelectedShare', total ? formatPercent(selected.total_sec / total * 100) : '%0,0');
    document.querySelectorAll('#dtReasonRows tr[data-code]').forEach(row => {
      row.classList.toggle('selected', row.dataset.code === state.selected);
    });
    renderDaily();
  }

  function selectReason(code) {
    state.selected = code || '';
    if ($('dtReasonSelect')) $('dtReasonSelect').value = state.selected;
    renderSelectedReason();
  }

  function renderReasonOptions() {
    const select = $('dtReasonSelect');
    if (!select) return;
    select.replaceChildren();
    const all = document.createElement('option');
    all.value = '';
    all.textContent = 'Tüm duruş sebepleri';
    select.appendChild(all);
    state.reasons.forEach(reason => {
      const option = document.createElement('option');
      option.value = reason.reason_code;
      option.textContent = `${reason.reason_name} · ${formatDuration(reason.total_sec)}`;
      select.appendChild(option);
    });
    if (!state.reasons.some(reason => reason.reason_code === state.selected)) state.selected = '';
    select.value = state.selected;
  }

  function renderReasonTable() {
    const body = $('dtReasonRows');
    if (!body) return;
    body.replaceChildren();
    const total = summarizeReasons(state.reasons).total_sec;
    if (!state.reasons.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 6;
      cell.className = 'empty';
      cell.textContent = 'Seçilen kapsamda duruş sebebi yok.';
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    state.reasons.forEach(reason => {
      const row = document.createElement('tr');
      row.dataset.code = reason.reason_code;
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.setAttribute('aria-label', `${reason.reason_name} duruşunu seç`);

      const nameCell = document.createElement('td');
      const name = document.createElement('div');
      name.className = 'reason-name-cell';
      const dot = document.createElement('span');
      dot.className = 'reason-dot';
      dot.style.background = reason.color_hex || '#ef4444';
      const label = document.createElement('span');
      label.textContent = reason.reason_name;
      name.append(dot, label);
      nameCell.appendChild(name);

      const categoryCell = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = 'category-badge';
      badge.textContent = reason.category === 'planned' ? 'Planlı' : 'Plansız';
      categoryCell.appendChild(badge);

      const values = [
        (Number(reason.count) || 0).toLocaleString('tr-TR'),
        formatDuration(reason.total_sec),
        formatDuration(reason.avg_sec),
        total ? formatPercent(reason.total_sec / total * 100) : '%0,0',
      ];
      row.append(nameCell, categoryCell);
      values.forEach(value => {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.appendChild(cell);
      });
      row.addEventListener('click', () => selectReason(reason.reason_code));
      row.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          selectReason(reason.reason_code);
        }
      });
      body.appendChild(row);
    });
  }

  function renderHeadline(oee) {
    const totals = oee.totals || {};
    const summary = summarizeReasons(state.reasons);
    const summaryCount = Number(totals.summary_count) || 0;
    const top = state.reasons[0];
    setText('dtGross', summaryCount ? formatDuration(totals.planned_base_sec) : '—');
    setText('dtGrossSub', summaryCount ? `Üretime ayrılan ${formatDuration(totals.planned_production_sec)}` : 'Vardiya özeti bulunamadı');
    setText('dtRun', summaryCount ? formatDuration(totals.run_time_sec) : '—');
    setText('dtRunSub', `${summaryCount} vardiya özeti · mola ${formatDuration(totals.break_sec)}`);
    setText('dtTotal', formatDuration(summary.total_sec));
    setText('dtTotalSub', `Plansız ${formatDuration(summary.unplanned_sec)} · planlı ${formatDuration(summary.planned_sec)}`);
    setText('dtAvailability', summaryCount ? formatPercent((oee.oee || {}).availability) : '—');
    setText('dtCount', summary.count.toLocaleString('tr-TR'));
    setText('dtCountSub', summary.count ? `Ortalama ${formatDuration(summary.total_sec / summary.count)}` : 'Kayıt yok');
    setText('dtTopReason', top ? top.reason_name : '—');
    setText('dtTopReasonSub', top ? `${formatDuration(top.total_sec)} · ${formatPercent(top.total_sec / Math.max(summary.total_sec, 1) * 100)}` : 'Kayıt yok');
  }

  function setLoading(isLoading) {
    state.loading = isLoading;
    const button = $('dtHistoryLoad');
    if (!button) return;
    button.disabled = isLoading;
    button.textContent = isLoading ? 'Hesaplanıyor…' : 'Analizi Göster';
  }

  async function load(force = false) {
    if (state.loading || (state.loaded && !force)) return;
    const start = $('dtHistoryStart').value;
    const end = $('dtHistoryEnd').value;
    if (!start || !end || start > end) {
      setText('dtHistoryStatus', 'Başlangıç tarihi bitiş tarihinden sonra olamaz.');
      return;
    }
    const span = Math.round((new Date(end + 'T00:00:00') - new Date(start + 'T00:00:00')) / 86400000);
    if (span > 366) {
      setText('dtHistoryStatus', 'Tek analizde en fazla 366 günlük tarih aralığı seçin.');
      return;
    }
    setLoading(true);
    setText('dtHistoryStatus', 'Duruş KPI metrikleri hesaplanıyor…');
    try {
      const shift = $('dtHistoryShift').value;
      const common = new URLSearchParams({line_id: String(window.LID || LID), date_from: start, date_to: end});
      if (shift) common.set('shift', shift);
      const [analysisResponse, oeeResponse] = await Promise.all([
        fetch(`${window.API || API}/api/v1/downtimes/analysis?${common}`),
        fetch(`${window.API || API}/api/v1/production/oee?${common}`),
      ]);
      if (!analysisResponse.ok || !oeeResponse.ok) throw new Error('API verisi alınamadı');
      const analysis = await analysisResponse.json();
      const oee = await oeeResponse.json();
      state.reasons = visibleReasons(analysis.reasons).sort((a, b) => b.total_sec - a.total_sec);
      state.daily = analysis.daily || [];
      renderHeadline(oee);
      renderReasonOptions();
      renderReasonTable();
      renderSelectedReason();
      const summaryCount = Number((oee.totals || {}).summary_count) || 0;
      setText(
        'dtHistoryStatus',
        `${formatDate(start)}–${formatDate(end)} · ${summaryCount} vardiya özeti. Net çalışma OEE özetinden; sebep süreleri tamamlanmış duruş kayıtlarından hesaplandı.`,
      );
      const url = new URL(location.href);
      url.searchParams.set('tab', 'downtime');
      url.searchParams.set('date_from', start);
      url.searchParams.set('date_to', end);
      history.replaceState(null, '', url);
      state.loaded = true;
    } catch (error) {
      setText('dtHistoryStatus', `Duruş analizi alınamadı: ${error.message}`);
      state.loaded = false;
    } finally {
      setLoading(false);
    }
  }

  function initialize() {
    const query = new URLSearchParams(location.search);
    const materialFilter = window.materialHistoryFilter ? window.materialHistoryFilter() : null;
    $('dtHistoryStart').value = query.get('date_from') || materialFilter?.start || daysAgo(15);
    $('dtHistoryEnd').value = query.get('date_to') || materialFilter?.end || localISO(new Date());
    $('dtHistoryLoad').addEventListener('click', () => load(true));
    $('dtReasonSelect').addEventListener('change', event => selectReason(event.target.value));
    window.loadDowntimeHistory = () => load(false);
    if (query.get('tab') === 'downtime' && window.switchTab) window.switchTab('downtime');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
