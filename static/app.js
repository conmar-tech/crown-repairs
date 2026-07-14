const state = {
    view: 'orders',
    orders: [],
    total: 0,
    limit: 50,
    offset: 0,
    status: '',
    nameQuery: '',
    phoneQuery: '',
    codeQuery: '',
    ordersPeriod: '',
    ordersDateFrom: '',
    ordersDateTo: '',
    clientKey: '',
    clientName: '',
    clientPhone: '',
    clients: [],
    clientsTotal: 0,
    clientsLimit: 100,
    clientsOffset: 0,
    clientsQuery: '',
    clientsSort: 'recent',
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

const statusLabels = {
    New: 'New',
    InWork: 'In Work',
    AtJeweler: 'At Jeweler',
    Ready: 'Ready',
    PickedUp: 'Picked Up',
};
const statusOptions = ['New', 'InWork', 'AtJeweler', 'Ready', 'PickedUp'];

const elements = {
    pageTitle: document.getElementById('page-title'),
    syncStatus: document.getElementById('sync-status'),
    refreshButton: document.getElementById('refresh-button'),
    ordersGrid: document.getElementById('orders-grid'),
    ordersCount: document.getElementById('orders-count'),
    ordersNameSearch: document.getElementById('orders-name-search'),
    ordersPhoneSearch: document.getElementById('orders-phone-search'),
    ordersCodeSearch: document.getElementById('orders-code-search'),
    ordersCodeClear: document.getElementById('orders-code-clear'),
    ordersDateFrom: document.getElementById('orders-date-from'),
    ordersDateTo: document.getElementById('orders-date-to'),
    ordersFilterForm: document.getElementById('orders-filter-form'),
    ordersReset: document.getElementById('orders-reset'),
    activeClientFilter: document.getElementById('active-client-filter'),
    activeClientFilterText: document.getElementById('active-client-filter-text'),
    clearClientFilter: document.getElementById('clear-client-filter'),
    ordersPrev: document.getElementById('orders-prev'),
    ordersNext: document.getElementById('orders-next'),
    ordersPaginationLabel: document.getElementById('orders-pagination-label'),
    clientsGrid: document.getElementById('clients-grid'),
    clientsCount: document.getElementById('clients-count'),
    clientsSearch: document.getElementById('clients-search'),
    clientsSort: document.getElementById('clients-sort'),
    clientsFilterForm: document.getElementById('clients-filter-form'),
    clientsReset: document.getElementById('clients-reset'),
    clientsPrev: document.getElementById('clients-prev'),
    clientsNext: document.getElementById('clients-next'),
    clientsPaginationLabel: document.getElementById('clients-pagination-label'),
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

function centsToMoneyInput(value) {
    return ((Number(value) || 0) / 100).toFixed(2);
}

function moneyInputToCents(value) {
    const normalized = String(value || '').trim().replace(/[$,\s]/g, '');
    if (!normalized) return 0;
    const parsed = Number(normalized);
    if (!Number.isFinite(parsed) || parsed < 0) {
        throw new Error('Enter a valid non-negative amount.');
    }
    return Math.round(parsed * 100);
}

function onlyDigits(value) {
    return String(value || '').replace(/\D/g, '');
}

function parseDateTime(value) {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function displayDateTime(value) {
    const parsed = parseDateTime(value);
    return parsed ? dateTimeFormat.format(parsed) : '-';
}

function displayDate(value) {
    if (!value) return '-';
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
    if (state.nameQuery) params.set('name', state.nameQuery);
    if (state.phoneQuery) params.set('phone', state.phoneQuery);
    if (state.codeQuery) params.set('code', state.codeQuery);
    if (state.ordersPeriod) params.set('period', state.ordersPeriod);
    if (state.ordersDateFrom) params.set('date_from', state.ordersDateFrom);
    if (state.ordersDateTo) params.set('date_to', state.ordersDateTo);
    if (state.clientKey) params.set('client_key', state.clientKey);
    if (state.clientName) params.set('client_name', state.clientName);
    if (state.clientPhone) params.set('client_phone', state.clientPhone);

    const data = await api(`/api/orders?${params}`);
    state.orders = data.items;
    state.total = data.total;
    renderStatusCounts(data.statusCounts || {});
    renderOrders();
    setConnectionState(true);
}

function renderStatusCounts(counts) {
    const all = Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
    const allNode = document.querySelector('[data-count="all"]');
    if (allNode) allNode.textContent = all;
    for (const status of Object.keys(statusLabels)) {
        const node = document.querySelector(`[data-count="${status}"]`);
        if (node) node.textContent = counts[status] || 0;
    }
}

function renderOrders() {
    elements.ordersGrid.replaceChildren();
    elements.ordersCount.textContent = `${state.total} orders`;
    renderActiveClientFilter();

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

function renderActiveClientFilter() {
    if (!state.clientKey) {
        elements.activeClientFilter.hidden = true;
        return;
    }
    const label = state.clientName || state.clientPhone || 'Selected client';
    const phone = state.clientPhone ? ` · ${state.clientPhone}` : '';
    elements.activeClientFilterText.textContent = `Orders for ${label}${phone}`;
    elements.activeClientFilter.hidden = false;
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
    const idRow = document.createElement('div');
    idRow.className = 'order-id-row';
    const id = document.createElement('span');
    id.className = 'order-id';
    id.textContent = order.orderId;
    const dateTime = document.createElement('span');
    dateTime.className = 'order-date';
    dateTime.textContent = displayDateTime(order.createdAt);
    idRow.append(id, dateTime);
    const name = document.createElement('h3');
    name.textContent = order.customerName || 'No customer name';
    const meta = document.createElement('div');
    meta.className = 'order-meta';
    meta.append(
        metaLine(order.customerPhone || 'No phone', 'customer-phone'),
        metaLine(order.customerAddress || 'No address'),
    );
    title.append(idRow, name, meta);

    const statusActions = document.createElement('div');
    statusActions.className = 'status-actions';
    const select = document.createElement('select');
    select.className = 'status-select';
    for (const value of statusOptions) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = statusLabels[value];
        option.selected = value === order.orderStatus;
        select.append(option);
    }
    select.addEventListener('change', () => changeStatus(order, select, card));
    statusActions.append(select);
    if (order.orderStatus === 'Ready') {
        const pickup = document.createElement('button');
        pickup.className = 'button pickup-button';
        pickup.type = 'button';
        pickup.textContent = 'Picked Up';
        pickup.addEventListener('click', () => markPickedUp(order));
        statusActions.append(pickup);
    }
    head.append(clientPhoto, title, statusActions);

    const body = document.createElement('div');
    body.className = 'order-body';
    const work = document.createElement('p');
    work.className = 'work-line';
    const itemType = order.itemType || firstItemType(order.workTemplates || []);
    const services = order.serviceNames || serviceNames(order.workTemplates || []);
    if (itemType) {
        const strong = document.createElement('strong');
        strong.className = 'item-type';
        strong.textContent = itemType;
        work.append(strong);
        if (services.length) work.append(document.createTextNode(` | ${services.join(', ')}`));
    } else {
        work.textContent = order.workDescription || 'Repair order';
    }

    const notes = order.manualWorkNotes || manualWorkNotes(order.workDescription || '', order.workTemplates || []);
    const payment = paymentPanel(order);

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

    body.append(work);
    if (notes) {
        const notesLine = document.createElement('p');
        notesLine.className = 'service-notes';
        notesLine.textContent = notes;
        body.append(notesLine);
    }
    body.append(payment);
    if (photos.childElementCount) body.append(photos);

    const footer = document.createElement('div');
    footer.className = 'order-footer';
    const sync = document.createElement('span');
    sync.className = `sync-chip ${order.syncState === 'Synced' ? 'synced' : 'pending'}`;
    sync.textContent = order.syncState || 'Synced';
    const footerActions = document.createElement('div');
    footerActions.className = 'card-actions';
    addFileLink(footerActions, order.signatureUrl, 'Signature');
    addPrintLink(footerActions, order.labelPdfUrl || order.labelPngUrl);
    const remove = document.createElement('button');
    remove.className = 'link-button danger';
    remove.type = 'button';
    remove.textContent = 'Delete';
    remove.addEventListener('click', () => deleteOrder(order));
    footerActions.append(remove);
    footer.append(sync, footerActions);

    card.append(head, body, footer);
    return card;
}

function metaLine(text, className = '') {
    const span = document.createElement('span');
    if (className) span.className = className;
    span.textContent = text;
    return span;
}

function initials(name) {
    return (name || 'C').trim().slice(0, 1).toUpperCase();
}

function firstItemType(templates) {
    const item = templates.find(value => value.startsWith('Item:'));
    return item ? item.replace(/^Item:\s*/, '') : (templates[0] || '');
}

function serviceNames(templates) {
    return templates.filter(value => value && !value.startsWith('Item:'));
}

function manualWorkNotes(description, templates) {
    const prefix = templates.join(', ');
    if (!prefix || !description.startsWith(prefix)) return '';
    return description.slice(prefix.length).replace(/^:\s*/, '').trim();
}

function moneyChip(label, value, extra = '') {
    const chip = document.createElement('span');
    chip.className = `money-chip ${extra}`;
    const labelNode = document.createElement('span');
    labelNode.textContent = `${label}: `;
    const valueNode = document.createElement('strong');
    valueNode.textContent = cents(value);
    chip.append(labelNode, valueNode);
    return chip;
}

function paymentPanel(order) {
    const container = document.createElement('div');
    container.className = 'payment-panel';
    renderPaymentSummary(container, order);
    return container;
}

function renderPaymentSummary(container, order) {
    const row = document.createElement('div');
    row.className = 'money-row';
    row.append(
        moneyChip('Price', order.totalPriceCents),
        moneyChip('Deposit', order.depositPaidCents),
    );
    if (Number(order.balanceDueCents || 0) > 0) {
        row.append(moneyChip('Due', order.balanceDueCents, 'due'));
    }

    const edit = document.createElement('button');
    edit.className = 'link-button payment-edit-button';
    edit.type = 'button';
    edit.textContent = 'Edit';
    edit.addEventListener('click', () => renderPaymentEditor(container, order));
    row.append(edit);
    container.replaceChildren(row);
}

function renderPaymentEditor(container, order) {
    const form = document.createElement('form');
    form.className = 'payment-editor';

    const totalInput = paymentInput('Price', order.totalPriceCents);
    const depositInput = paymentInput('Deposit', order.depositPaidCents);
    const due = document.createElement('span');
    due.className = 'payment-due-preview';

    const updateDue = () => {
        try {
            const total = moneyInputToCents(totalInput.input.value);
            const deposit = moneyInputToCents(depositInput.input.value);
            due.textContent = `Due ${cents(Math.max(total - deposit, 0))}`;
            due.classList.toggle('due', total > deposit);
        } catch {
            due.textContent = 'Due -';
            due.classList.remove('due');
        }
    };
    totalInput.input.addEventListener('input', updateDue);
    depositInput.input.addEventListener('input', updateDue);
    updateDue();

    const save = document.createElement('button');
    save.className = 'button primary small';
    save.type = 'submit';
    save.textContent = 'Save';

    const cancel = document.createElement('button');
    cancel.className = 'button secondary small';
    cancel.type = 'button';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => renderPaymentSummary(container, order));

    form.append(totalInput.label, depositInput.label, due, save, cancel);
    form.addEventListener('submit', async event => {
        event.preventDefault();
        let totalPriceCents;
        let depositPaidCents;
        try {
            totalPriceCents = moneyInputToCents(totalInput.input.value);
            depositPaidCents = moneyInputToCents(depositInput.input.value);
        } catch (error) {
            showToast(error.message, true);
            return;
        }

        save.disabled = true;
        cancel.disabled = true;
        try {
            const updated = await updateOrderPayment(order, totalPriceCents, depositPaidCents);
            Object.assign(order, updated);
            showToast(`Updated payment for ${order.orderId}`);
            await reloadAfterMutation();
        } catch (error) {
            showToast(error.message, true);
            save.disabled = false;
            cancel.disabled = false;
        }
    });

    container.replaceChildren(form);
    totalInput.input.focus();
    totalInput.input.select();
}

function paymentInput(labelText, centsValue) {
    const label = document.createElement('label');
    label.className = 'payment-input';
    const text = document.createElement('span');
    text.textContent = labelText;
    const input = document.createElement('input');
    input.type = 'number';
    input.min = '0';
    input.step = '0.01';
    input.inputMode = 'decimal';
    input.value = centsToMoneyInput(centsValue);
    label.append(text, input);
    return {label, input};
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

function addPrintLink(container, url) {
    const link = document.createElement('a');
    link.className = `button secondary small${url ? '' : ' disabled'}`;
    link.textContent = 'Print';
    if (url) {
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
    } else {
        link.setAttribute('aria-disabled', 'true');
    }
    container.append(link);
}

async function changeStatus(order, select, card) {
    const previous = order.orderStatus;
    let settleBalance = false;
    if (select.value === 'PickedUp' && Number(order.balanceDueCents || 0) > 0) {
        settleBalance = window.confirm('Release and close balance?');
        if (!settleBalance) {
            select.value = previous;
            return;
        }
    }

    select.disabled = true;
    try {
        const updated = await updateOrderStatus(order, select.value, settleBalance);
        Object.assign(order, updated);
        card.dataset.status = order.orderStatus;
        showToast(`Updated ${order.orderId} to ${statusLabels[order.orderStatus]}`);
        await reloadAfterMutation();
    } catch (error) {
        select.value = previous;
        showToast(error.message, true);
    } finally {
        select.disabled = false;
    }
}

async function markPickedUp(order) {
    let settleBalance = false;
    if (Number(order.balanceDueCents || 0) > 0) {
        settleBalance = window.confirm('Release and close balance?');
        if (!settleBalance) return;
    }
    try {
        await updateOrderStatus(order, 'PickedUp', settleBalance);
        showToast(`Updated ${order.orderId} to Picked Up`);
        await reloadAfterMutation();
    } catch (error) {
        showToast(error.message, true);
    }
}

function updateOrderStatus(order, status, settleBalance = false) {
    return api(`/api/orders/${encodeURIComponent(order.id)}/status`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json', 'X-Requested-With': 'CrownRepairs'},
        body: JSON.stringify({status, settleBalance}),
    });
}

function updateOrderPayment(order, totalPriceCents, depositPaidCents) {
    return api(`/api/orders/${encodeURIComponent(order.id)}/payment`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json', 'X-Requested-With': 'CrownRepairs'},
        body: JSON.stringify({totalPriceCents, depositPaidCents}),
    });
}

async function deleteOrder(order) {
    const confirmed = window.confirm(`Delete order ${order.orderId} from Firestore?`);
    if (!confirmed) return;
    try {
        await api(`/api/orders/${encodeURIComponent(order.id)}`, {
            method: 'DELETE',
            headers: {'X-Requested-With': 'CrownRepairs'},
        });
        showToast(`Deleted ${order.orderId}`);
        await reloadAfterMutation();
    } catch (error) {
        showToast(error.message, true);
    }
}

async function reloadAfterMutation() {
    await Promise.all([loadOrders(), loadFinance(), loadClients()]);
}

async function loadClients() {
    const params = new URLSearchParams({
        limit: state.clientsLimit,
        offset: state.clientsOffset,
        sort: state.clientsSort,
    });
    if (state.clientsQuery) params.set('q', state.clientsQuery);
    const data = await api(`/api/clients?${params}`);
    state.clients = data.items;
    state.clientsTotal = data.total;
    renderClients();
    setConnectionState(true);
}

function renderClients() {
    elements.clientsGrid.replaceChildren();
    elements.clientsCount.textContent = `${state.clientsTotal} clients`;
    if (!state.clients.length) {
        const empty = document.createElement('article');
        empty.className = 'empty-state';
        empty.textContent = 'No clients found.';
        elements.clientsGrid.append(empty);
    }
    for (const client of state.clients) {
        elements.clientsGrid.append(clientCard(client));
    }
    const first = state.clientsTotal ? state.clientsOffset + 1 : 0;
    const last = Math.min(state.clientsOffset + state.clientsLimit, state.clientsTotal);
    elements.clientsPaginationLabel.textContent = `${first}-${last} of ${state.clientsTotal}`;
    elements.clientsPrev.disabled = state.clientsOffset === 0;
    elements.clientsNext.disabled = state.clientsOffset + state.clientsLimit >= state.clientsTotal;
}

function clientCard(client) {
    const card = document.createElement('button');
    card.className = 'client-card';
    card.type = 'button';
    card.addEventListener('click', () => openClientOrders(client));

    const photo = document.createElement('span');
    photo.className = 'client-list-photo';
    if (client.latestClientPhotoUrl) {
        const image = document.createElement('img');
        image.src = client.latestClientPhotoUrl;
        image.alt = '';
        photo.append(image);
    } else {
        photo.textContent = initials(client.name);
    }

    const body = document.createElement('span');
    body.className = 'client-card-body';
    const name = document.createElement('strong');
    name.textContent = client.name || 'No customer name';
    const contact = document.createElement('span');
    contact.className = 'client-contact';
    const phone = document.createElement('strong');
    phone.className = 'client-phone';
    phone.textContent = client.phone || 'No phone';
    contact.append(phone);
    if (client.address) {
        contact.append(document.createTextNode(` · ${client.address}`));
    }
    const meta = document.createElement('span');
    meta.className = 'client-meta';
    meta.append(contact);
    const chips = document.createElement('span');
    chips.className = 'client-chips';
    if (Number(client.orderCount || 0) > 0) {
        chips.append(clientChip(`${client.orderCount} orders`, 'orders'));
    }
    if (Number(client.dueCents || 0) > 0) {
        chips.append(clientChip(`Due ${cents(client.dueCents)}`, 'due'));
    }
    if (client.lastOrderAt) {
        chips.append(clientChip(`Last ${displayDateTime(client.lastOrderAt)}`, ''));
    }
    body.append(name, meta);
    if (chips.childElementCount) body.append(chips);

    card.append(photo, body);
    return card;
}

function clientChip(text, extra) {
    const chip = document.createElement('span');
    chip.className = `client-chip ${extra}`;
    chip.textContent = text;
    return chip;
}

async function openClientOrders(client) {
    state.clientKey = client.key || '';
    state.clientName = client.name || '';
    state.clientPhone = client.phone || '';
    state.offset = 0;
    switchView('orders');
    try {
        await loadOrders();
    } catch (error) {
        setConnectionState(false);
        showToast(error.message, true);
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
    elements.pageTitle.textContent = {orders: 'Orders', clients: 'Clients', finances: 'Finances'}[view] || 'Orders';
}

function setStatusFilter(status) {
    state.status = status;
    document.querySelectorAll('.status-card').forEach(item => item.classList.toggle('active', item.dataset.status === status));
}

function setOrdersPeriod(period) {
    state.ordersPeriod = state.ordersPeriod === period ? '' : period;
    state.ordersDateFrom = '';
    state.ordersDateTo = '';
    elements.ordersDateFrom.value = '';
    elements.ordersDateTo.value = '';
    document.querySelectorAll('.date-filter-tab').forEach(item => item.classList.toggle('active', item.dataset.period === state.ordersPeriod));
}

function applyOrderInputState() {
    state.nameQuery = elements.ordersNameSearch.value.trim();
    state.phoneQuery = elements.ordersPhoneSearch.value.trim();
    state.codeQuery = elements.ordersCodeSearch.value.trim();
    state.offset = 0;
}

let ordersSearchTimer;
function scheduleOrdersSearch() {
    clearTimeout(ordersSearchTimer);
    ordersSearchTimer = setTimeout(async () => {
        applyOrderInputState();
        try {
            await loadOrders();
        } catch (error) {
            setConnectionState(false);
            showToast(error.message, true);
        }
    }, 220);
}

async function applyOrdersDateFilter() {
    if (elements.ordersDateFrom.value && elements.ordersDateTo.value && elements.ordersDateFrom.value > elements.ordersDateTo.value) {
        showToast('Start date cannot be after end date', true);
        return;
    }
    applyOrderInputState();
    state.ordersDateFrom = elements.ordersDateFrom.value;
    state.ordersDateTo = elements.ordersDateTo.value;
    state.ordersPeriod = '';
    document.querySelectorAll('.date-filter-tab').forEach(item => item.classList.remove('active'));
    try {
        await loadOrders();
    } catch (error) {
        setConnectionState(false);
        showToast(error.message, true);
    }
}

for (const input of [elements.ordersNameSearch, elements.ordersPhoneSearch, elements.ordersCodeSearch]) {
    input.addEventListener('input', scheduleOrdersSearch);
}

for (const input of [elements.ordersDateFrom, elements.ordersDateTo]) {
    input.addEventListener('change', applyOrdersDateFilter);
}

document.querySelectorAll('.nav-item').forEach(button => {
    button.addEventListener('click', () => switchView(button.dataset.view));
});

document.querySelectorAll('.status-card').forEach(button => {
    button.addEventListener('click', async () => {
        setStatusFilter(button.dataset.status);
        state.offset = 0;
        try {
            await loadOrders();
        } catch (error) {
            setConnectionState(false);
            showToast(error.message, true);
        }
    });
});

document.querySelectorAll('.date-filter-tab').forEach(button => {
    button.addEventListener('click', async () => {
        setOrdersPeriod(button.dataset.period);
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
    await applyOrdersDateFilter();
});

elements.ordersReset.addEventListener('click', async () => {
    elements.ordersFilterForm.reset();
    state.nameQuery = '';
    state.phoneQuery = '';
    state.codeQuery = '';
    state.ordersDateFrom = '';
    state.ordersDateTo = '';
    state.ordersPeriod = '';
    state.clientKey = '';
    state.clientName = '';
    state.clientPhone = '';
    state.offset = 0;
    setStatusFilter('');
    document.querySelectorAll('.date-filter-tab').forEach(item => item.classList.remove('active'));
    try {
        await loadOrders();
    } catch (error) {
        showToast(error.message, true);
    }
});

elements.ordersCodeClear.addEventListener('click', async () => {
    elements.ordersCodeSearch.value = '';
    state.codeQuery = '';
    state.offset = 0;
    await loadOrders().catch(error => showToast(error.message, true));
});

elements.clearClientFilter.addEventListener('click', async () => {
    state.clientKey = '';
    state.clientName = '';
    state.clientPhone = '';
    state.offset = 0;
    await loadOrders().catch(error => showToast(error.message, true));
});

elements.ordersPrev.addEventListener('click', async () => {
    state.offset = Math.max(0, state.offset - state.limit);
    await loadOrders().catch(error => showToast(error.message, true));
});

elements.ordersNext.addEventListener('click', async () => {
    state.offset += state.limit;
    await loadOrders().catch(error => showToast(error.message, true));
});

let clientsSearchTimer;
elements.clientsSearch.addEventListener('input', () => {
    clearTimeout(clientsSearchTimer);
    clientsSearchTimer = setTimeout(async () => {
        state.clientsQuery = elements.clientsSearch.value.trim();
        state.clientsOffset = 0;
        try {
            await loadClients();
        } catch (error) {
            setConnectionState(false);
            showToast(error.message, true);
        }
    }, 220);
});

elements.clientsFilterForm.addEventListener('submit', async event => {
    event.preventDefault();
    state.clientsQuery = elements.clientsSearch.value.trim();
    state.clientsSort = elements.clientsSort.value;
    state.clientsOffset = 0;
    await loadClients().catch(error => showToast(error.message, true));
});

elements.clientsSort.addEventListener('change', async () => {
    state.clientsSort = elements.clientsSort.value;
    state.clientsOffset = 0;
    await loadClients().catch(error => showToast(error.message, true));
});

elements.clientsReset.addEventListener('click', async () => {
    elements.clientsFilterForm.reset();
    state.clientsQuery = '';
    state.clientsSort = 'recent';
    state.clientsOffset = 0;
    await loadClients().catch(error => showToast(error.message, true));
});

elements.clientsPrev.addEventListener('click', async () => {
    state.clientsOffset = Math.max(0, state.clientsOffset - state.clientsLimit);
    await loadClients().catch(error => showToast(error.message, true));
});

elements.clientsNext.addEventListener('click', async () => {
    state.clientsOffset += state.clientsLimit;
    await loadClients().catch(error => showToast(error.message, true));
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
        await Promise.all([loadOrders(), loadFinance(), loadClients()]);
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

Promise.all([loadOrders(), loadFinance(), loadClients()]).catch(error => {
    setConnectionState(false);
    showToast(error.message || 'Could not load data', true);
});
