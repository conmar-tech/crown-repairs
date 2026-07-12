const state = {
    view: 'orders',
    orders: [],
    total: 0,
    limit: 50,
    offset: 0,
    status: '',
    query: '',
    ordersDateFrom: '',
    ordersDateTo: '',
    financePeriod: 'month',
    chartPeriod: 'month',
    financeDateFrom: '',
    financeDateTo: '',
    finance: null,
};

const money = new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'});
const number = new Intl.NumberFormat('en-US', {maximumFractionDigits: 0});
const compactNumber = new Intl.NumberFormat('en-US', {maximumFractionDigits: 1});
const dateTimeFormat = new Intl.DateTimeFormat('en-US', {month: 'short', day: '2-digit', year: 'numeric', hour: 'numeric', minute: '2-digit'});
const dateFormat = new Intl.DateTimeFormat('en-US', {month: 'short', day: '2-digit', year: 'numeric'});
const svgNS = 'http://www.w3.org/2000/svg';
const statusLabels = {New: 'New', InWork: 'In work', Ready: 'Ready', PickedUp: 'Picked up'};

const elements = {
    pageTitle: document.getElementById('page-title'),
    syncStatus: document.getElementById('sync-status'),
    refreshButton: document.getElementById('refresh-button'),
    ordersGrid: document.getElementById('orders-grid'),
    ordersCount: document.getElementById('orders-count'),
    ordersSearch: document.getElementById('orders-search'),
    ordersDateFrom: document.getElementById('orders-date-from'),
    ordersDateTo: document.getElementById('orders-date-to'),
    ordersFilterForm: document.getElementById('orders-filter-form'),
    ordersReset: document.getElementById('orders-reset'),
    ordersPrev: document.getElementById('orders-prev'),
    ordersNext: document.getElementById('orders-next'),
    ordersPaginationLabel: document.getElementById('orders-pagination-label'),
    financeFilterForm: document.getElementById('finance-filter-form'),
    financeDateFrom: document.getElementById('finance-date-from'),
    financeDateTo: document.getElementById('finance-date-to'),
    financeReset: document.getElementById('finance-reset'),
    chart: document.getElementById('orders-chart'),
    chartTitle: document.getElementById('chart-title'),
    chartEmpty: document.getElementById('chart-empty'),
    chartTooltip: document.getElementById('chart-tooltip'),
    photoDialog: document.getElementById('photo-dialog'),
    photoPreview: document.getElementById('photo-preview'),
    closePhoto: document.getElementById('close-photo'),
    toast: document.getElementById('toast'),
};

function cents(value) {
    return money.format((Number(value) || 0) / 100);
}

function parseDateTime(value) {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function displayDateTime(value) {
    const parsed = parseDateTime(value);
    return parsed ? dateTimeFormat.format(parsed) : '—';
}

function displayDate(value) {
    if (!value) return '';
    const parsed = new Date(`${value}T12:00:00`);
    return Number.isNaN(parsed.getTime()) ? value : dateFormat.format(parsed);
}

async function api(path, options = {}) {
    const response = await fetch(path, {credentials: 'same-origin', ...options});
    if (response.status === 401) {
        window.location.assign('/login');
        throw new Error('Authentication required');
    }
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed: ${response.status}`);
    }
    if (response.status === 204) return null;
    return response.json();
}

function setConnectionState(ok) {
    elements.syncStatus.classList.toggle('error', !ok);
    elements.syncStatus.lastChild.textContent = ok ? ' Live Firestore' : ' Firestore error';
}

async function loadOrders() {
    const params = new URLSearchParams({limit: state.limit, offset: state.offset});
    if (state.status) params.set('status', state.status);
    if (state.query) params.set('q', state.query);
    if (state.ordersDateFrom) params.set('date_from', state.ordersDateFrom);
    if (state.ordersDateTo) params.set('date_to', state.ordersDateTo);
    const data = await api(`/api/orders?${params}`);
    state.orders = data.items;
    state.total = data.total;
    renderStatusCounts(data.statusCounts || {});
    renderOrders();
    setConnectionState(true);
}

function renderStatusCounts(counts) {
    const all = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
    document.querySelector('[data-count="all"]').textContent = all;
    for (const status of Object.keys(statusLabels)) {
        const node = document.querySelector(`[data-count="${status}"]`);
        if (node) node.textContent = counts[status] || 0;
    }
}

function renderOrders() {
    elements.ordersGrid.replaceChildren();
    elements.ordersCount.textContent = `${state.total} orders`;
    if (!state.orders.length) {
        const empty = document.createElement('article');
        empty.className = 'empty-state';
        empty.textContent = 'No repair orders found.';
        elements.ordersGrid.append(empty);
    }
    for (const order of state.orders) {
        elements.ordersGrid.append(orderCard(order));
    }
    const first = state.total ? state.offset + 1 : 0;
    const last = Math.min(state.offset + state.limit, state.total);
    elements.ordersPaginationLabel.textContent = `${first}-${last} of ${state.total}`;
    elements.ordersPrev.disabled = state.offset === 0;
    elements.ordersNext.disabled = state.offset + state.limit >= state.total;
}

function orderCard(order) {
    const card = document.createElement('article');
    card.className = 'order-card';
    card.dataset.status = order.orderStatus;

    const head = document.createElement('div');
    head.className = 'order-head';
    const clientPhoto = document.createElement('button');
    clientPhoto.className = 'client-photo';
    clientPhoto.type = 'button';
    const clientUrl = (order.clientPhotoUrls || [])[0];
    if (clientUrl) {
        const image = document.createElement('img');
        image.src = clientUrl;
        image.alt = '';
        clientPhoto.append(image);
        clientPhoto.addEventListener('click', () => openPhoto(clientUrl, `${order.customerName} photo`));
    } else {
        clientPhoto.textContent = initials(order.customerName);
        clientPhoto.disabled = true;
    }

    const title = document.createElement('div');
    title.className = 'order-title';
    const id = document.createElement('span');
    id.className = 'order-id';
    id.textContent = order.orderId;
    const name = document.createElement('h3');
    name.textContent = order.customerName || 'No customer name';
    const meta = document.createElement('div');
    meta.className = 'order-meta';
    meta.append(
        metaLine(order.customerPhone || 'No phone'),
        metaLine(displayDateTime(order.createdAt)),
        metaLine(order.customerAddress || 'No address'),
    );
    title.append(id, name, meta);

    const select = document.createElement('select');
    select.className = 'status-select';
    for (const [value, label] of Object.entries(statusLabels)) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        option.selected = value === order.orderStatus;
        select.append(option);
    }
    select.addEventListener('change', () => changeStatus(order, select, card));
    head.append(clientPhoto, title, select);

    const body = document.createElement('div');
    body.className = 'order-body';
    const work = document.createElement('p');
    work.className = 'work-line';
    const templates = order.workTemplates || [];
    const itemType = templates[0] || '';
    const templateText = templates.join(', ');
    const description = order.workDescription || '';
    const manualDescription = description.startsWith(templateText)
        ? description.slice(templateText.length).replace(/^:\s*/, '').trim()
        : description;
    if (itemType) {
        const strong = document.createElement('span');
        strong.className = 'item-type';
        strong.textContent = itemType;
        work.append(strong, document.createTextNode(templates.length > 1 ? ` · ${templates.slice(1).join(', ')}` : ''));
        if (manualDescription) work.append(document.createTextNode(` — ${manualDescription}`));
    } else {
        work.textContent = description || 'Repair order';
    }

    const moneyRow = document.createElement('div');
    moneyRow.className = 'money-row';
    moneyRow.append(
        moneyChip('Total', order.totalPriceCents),
        moneyChip('Deposit', order.depositPaidCents),
        moneyChip('Due', order.balanceDueCents, 'due'),
    );

    const photos = document.createElement('div');
    photos.className = 'photo-strip';
    for (const [index, url] of (order.itemPhotoUrls || []).entries()) {
        const thumb = document.createElement('button');
        thumb.className = 'thumb';
        thumb.type = 'button';
        const image = document.createElement('img');
        image.src = url;
        image.alt = `Jewelry ${index + 1}`;
        thumb.append(image);
        thumb.addEventListener('click', () => openPhoto(url, image.alt));
        photos.append(thumb);
    }

    const links = document.createElement('div');
    links.className = 'file-links';
    addFileLink(links, order.signatureUrl, 'Signature');
    addFileLink(links, order.labelPdfUrl, 'Label PDF');
    addFileLink(links, order.labelPngUrl, 'Label PNG');

    body.append(work, moneyRow);
    if (photos.childElementCount) body.append(photos);
    if (links.childElementCount) body.append(links);
    card.append(head, body);
    return card;
}

function metaLine(text) {
    const span = document.createElement('span');
    span.textContent = text;
    return span;
}

function initials(name) {
    return (name || 'C').trim().slice(0, 1).toUpperCase();
}

function moneyChip(label, value, extra = '') {
    const chip = document.createElement('span');
    chip.className = `money-chip ${extra}`;
    chip.innerHTML = `${label}: <strong>${cents(value)}</strong>`;
    return chip;
}

function addFileLink(container, url, label) {
    if (!url) return;
    const link = document.createElement('a');
    link.href = url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = label;
    container.append(link);
}

async function changeStatus(order, select, card) {
    const previous = order.orderStatus;
    select.disabled = true;
    try {
        const updated = await api(`/api/orders/${encodeURIComponent(order.id)}/status`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json', 'X-Requested-With': 'CrownRepairs'},
            body: JSON.stringify({status: select.value}),
        });
        Object.assign(order, updated);
        card.dataset.status = order.orderStatus;
        showToast(`Updated ${order.orderId} to ${statusLabels[order.orderStatus]}`);
        await Promise.all([loadOrders(), loadFinance()]);
    } catch (error) {
        select.value = previous;
        showToast(error.message, true);
    } finally {
        select.disabled = false;
    }
}

async function loadFinance() {
    const params = new URLSearchParams({period: state.financePeriod});
    if (state.financeDateFrom) params.set('date_from', state.financeDateFrom);
    if (state.financeDateTo) params.set('date_to', state.financeDateTo);
    state.finance = await api(`/api/finance?${params}`);
    renderFinance();
    setConnectionState(true);
}

function renderFinance() {
    if (!state.finance) return;
    const summary = state.finance.summary || {};
    for (const key of ['totalValueCents', 'openValueCents', 'readyValueCents', 'dueCents']) {
        const node = document.querySelector(`[data-metric="${key}"]`);
        node.textContent = cents(summary[key]);
        node.classList.remove('skeleton');
    }
    const ordersNode = document.querySelector('[data-metric-sub="orders"]');
    ordersNode.textContent = `${summary.orders || 0} orders · deposits ${cents(summary.depositCents)}`;
    renderChart();
}

function svgElement(name, attrs = {}) {
    const node = document.createElementNS(svgNS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}

function chartTitle(period) {
    return {week: 'Current week', month: 'Current month', year: 'Current year'}[period];
}

function chartValue(valueCents) {
    const value = valueCents / 100;
    if (value >= 1_000_000) return `$${compactNumber.format(value / 1_000_000)}m`;
    if (value >= 1_000) return `$${compactNumber.format(value / 1_000)}k`;
    return `$${number.format(value)}`;
}

function renderChart() {
    if (!state.finance) return;
    const series = state.finance.series[state.chartPeriod] || [];
    const hasData = series.some(item => item.valueCents !== 0);
    elements.chart.replaceChildren();
    elements.chartTitle.textContent = chartTitle(state.chartPeriod);
    elements.chartEmpty.hidden = hasData;
    elements.chart.hidden = !hasData;
    if (!hasData) return;

    const width = 1000;
    const height = 330;
    const margin = {top: 36, right: 22, bottom: 48, left: 66};
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const maxValue = Math.max(...series.map(item => item.valueCents), 1);
    const magnitude = Math.pow(10, Math.floor(Math.log10(maxValue)));
    const roundedMax = Math.ceil(maxValue / magnitude) * magnitude;

    for (let index = 0; index <= 4; index += 1) {
        const y = margin.top + (plotHeight / 4) * index;
        const value = roundedMax * (1 - index / 4);
        elements.chart.append(svgElement('line', {x1: margin.left, y1: y, x2: width - margin.right, y2: y, class: 'chart-grid'}));
        const label = svgElement('text', {x: margin.left - 12, y: y + 4, class: 'chart-axis-label', 'text-anchor': 'end'});
        label.textContent = chartValue(value);
        elements.chart.append(label);
    }

    const slot = plotWidth / series.length;
    const barWidth = Math.max(4, Math.min(46, slot * 0.62));
    const labelEvery = series.length > 24 ? Math.ceil(series.length / 12) : series.length > 14 ? 2 : 1;
    series.forEach((item, index) => {
        const barHeight = (item.valueCents / roundedMax) * plotHeight;
        const x = margin.left + index * slot + (slot - barWidth) / 2;
        const y = margin.top + plotHeight - barHeight;
        const bar = svgElement('rect', {
            x, y, width: barWidth, height: Math.max(barHeight, item.valueCents ? 2 : 0), rx: Math.min(4, barWidth / 5),
            class: 'chart-bar', tabindex: '0', 'aria-label': `${item.label}: ${cents(item.valueCents)}`,
        });
        bar.addEventListener('pointerenter', event => showChartTooltip(event, item));
        bar.addEventListener('pointermove', event => showChartTooltip(event, item));
        bar.addEventListener('pointerleave', hideChartTooltip);
        elements.chart.append(bar);

        if (item.valueCents) {
            const labelY = Math.max(margin.top - 7, y - 7);
            const valueLabel = svgElement('text', {
                x: x + barWidth / 2,
                y: labelY,
                class: `chart-value-label${series.length > 14 ? ' dense' : ''}`,
            });
            if (series.length > 14) {
                valueLabel.setAttribute('text-anchor', 'start');
                valueLabel.setAttribute('transform', `rotate(-55 ${x + barWidth / 2} ${labelY})`);
            }
            valueLabel.textContent = chartValue(item.valueCents);
            elements.chart.append(valueLabel);
        }

        if (index % labelEvery === 0 || index === series.length - 1) {
            const label = svgElement('text', {x: x + barWidth / 2, y: height - 22, class: 'chart-label'});
            label.textContent = item.label;
            elements.chart.append(label);
        }
    });
}

function showChartTooltip(event, item) {
    const wrap = event.currentTarget.closest('.chart-wrap').getBoundingClientRect();
    elements.chartTooltip.replaceChildren();
    const label = document.createElement('span');
    label.textContent = `${item.label} · ${item.detail}`;
    const value = document.createElement('strong');
    value.textContent = cents(item.valueCents);
    elements.chartTooltip.append(label, value);
    elements.chartTooltip.style.left = `${event.clientX - wrap.left}px`;
    elements.chartTooltip.style.top = `${event.clientY - wrap.top}px`;
    elements.chartTooltip.hidden = false;
}

function hideChartTooltip() {
    elements.chartTooltip.hidden = true;
}

function openPhoto(url, alt) {
    elements.photoPreview.src = url;
    elements.photoPreview.alt = alt;
    elements.photoDialog.showModal();
}

function closePhoto() {
    elements.photoDialog.close();
    elements.photoPreview.removeAttribute('src');
}

let toastTimer;
function showToast(message, isError = false) {
    clearTimeout(toastTimer);
    elements.toast.textContent = message;
    elements.toast.classList.toggle('error', isError);
    elements.toast.hidden = false;
    toastTimer = setTimeout(() => { elements.toast.hidden = true; }, 3500);
}

function switchView(view) {
    state.view = view;
    document.querySelectorAll('.content-section').forEach(section => section.classList.remove('active'));
    document.getElementById(`${view}-view`).classList.add('active');
    document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
    elements.pageTitle.textContent = view === 'orders' ? 'Orders' : 'Finances';
}

let searchTimer;
elements.ordersSearch.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        state.query = elements.ordersSearch.value.trim();
        state.offset = 0;
        try {
            await loadOrders();
        } catch (error) {
            setConnectionState(false);
            showToast(error.message, true);
        }
    }, 250);
});

document.querySelectorAll('.nav-item').forEach(button => {
    button.addEventListener('click', () => switchView(button.dataset.view));
});

document.querySelectorAll('.status-card').forEach(button => {
    button.addEventListener('click', async () => {
        document.querySelectorAll('.status-card').forEach(item => item.classList.toggle('active', item === button));
        state.status = button.dataset.status;
        state.offset = 0;
        try {
            await loadOrders();
        } catch (error) {
            setConnectionState(false);
            showToast(error.message, true);
        }
    });
});

elements.ordersFilterForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (elements.ordersDateFrom.value && elements.ordersDateTo.value && elements.ordersDateFrom.value > elements.ordersDateTo.value) {
        showToast('Start date cannot be after end date', true);
        return;
    }
    state.ordersDateFrom = elements.ordersDateFrom.value;
    state.ordersDateTo = elements.ordersDateTo.value;
    state.query = elements.ordersSearch.value.trim();
    state.offset = 0;
    try {
        await loadOrders();
    } catch (error) {
        setConnectionState(false);
        showToast(error.message, true);
    }
});

elements.ordersReset.addEventListener('click', async () => {
    elements.ordersFilterForm.reset();
    state.query = '';
    state.ordersDateFrom = '';
    state.ordersDateTo = '';
    state.offset = 0;
    try {
        await loadOrders();
    } catch (error) {
        showToast(error.message, true);
    }
});

elements.ordersPrev.addEventListener('click', async () => {
    state.offset = Math.max(0, state.offset - state.limit);
    await loadOrders().catch(error => showToast(error.message, true));
});

elements.ordersNext.addEventListener('click', async () => {
    state.offset += state.limit;
    await loadOrders().catch(error => showToast(error.message, true));
});

document.querySelectorAll('.period-tab').forEach(button => {
    button.addEventListener('click', async () => {
        state.financePeriod = button.dataset.period;
        state.financeDateFrom = '';
        state.financeDateTo = '';
        elements.financeFilterForm.reset();
        document.querySelectorAll('.period-tab').forEach(item => item.classList.toggle('active', item === button));
        try {
            await loadFinance();
        } catch (error) {
            setConnectionState(false);
            showToast(error.message, true);
        }
    });
});

document.querySelectorAll('.chart-tab').forEach(button => {
    button.addEventListener('click', () => {
        state.chartPeriod = button.dataset.chart;
        document.querySelectorAll('.chart-tab').forEach(item => item.classList.toggle('active', item === button));
        hideChartTooltip();
        renderChart();
    });
});

elements.financeFilterForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (elements.financeDateFrom.value && elements.financeDateTo.value && elements.financeDateFrom.value > elements.financeDateTo.value) {
        showToast('Start date cannot be after end date', true);
        return;
    }
    state.financeDateFrom = elements.financeDateFrom.value;
    state.financeDateTo = elements.financeDateTo.value;
    try {
        await loadFinance();
    } catch (error) {
        setConnectionState(false);
        showToast(error.message, true);
    }
});

elements.financeReset.addEventListener('click', async () => {
    elements.financeFilterForm.reset();
    state.financeDateFrom = '';
    state.financeDateTo = '';
    try {
        await loadFinance();
    } catch (error) {
        showToast(error.message, true);
    }
});

elements.refreshButton.addEventListener('click', async () => {
    elements.refreshButton.disabled = true;
    try {
        await Promise.all([loadOrders(), loadFinance()]);
        showToast('Data refreshed');
    } catch (error) {
        setConnectionState(false);
        showToast(error.message, true);
    } finally {
        elements.refreshButton.disabled = false;
    }
});

elements.closePhoto.addEventListener('click', closePhoto);
elements.photoDialog.addEventListener('click', event => {
    if (event.target === elements.photoDialog) closePhoto();
});

Promise.all([loadOrders(), loadFinance()]).catch(error => {
    setConnectionState(false);
    showToast(error.message || 'Could not load data', true);
});
