/**
 * Concord Dashboard Application
 * High-Contention Inventory Reservation & Live Contention Stream Client
 */

let eventSource = null;
let activeHoldTimer = null;
let currentActiveHold = null;

// Utility functions
function formatTime(isoStr) {
  if (!isoStr) return '--:--:--';
  const d = new Date(isoStr);
  return d.toTimeString().split(' ')[0];
}

function generateUUID() {
  return 'idemp-' + 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerText = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 4000);
}

// 1. Health Checks
async function checkHealth() {
  try {
    const res = await fetch('/health');
    const data = await res.json();
    const mongoChip = document.getElementById('mongo-status-chip');
    const mongoText = document.getElementById('mongo-status-text');
    const redisChip = document.getElementById('redis-status-chip');
    const redisText = document.getElementById('redis-status-text');

    if (data.components.mongodb === 'UP') {
      mongoChip.className = 'status-chip up';
      mongoText.innerText = 'Connected';
    } else {
      mongoChip.className = 'status-chip down';
      mongoText.innerText = 'Down';
    }

    if (data.components.redis === 'UP') {
      redisChip.className = 'status-chip up';
      redisText.innerText = 'Connected';
    } else {
      redisChip.className = 'status-chip down';
      redisText.innerText = 'Down';
    }
  } catch (err) {
    console.error('Health check failed', err);
  }
}

// 2. Fetch and render Products
async function loadProducts() {
  try {
    const res = await fetch('/products');
    const products = await res.json();
    const container = document.getElementById('products-container');
    const select = document.getElementById('res-product-select');
    document.getElementById('product-count-badge').innerText = `${products.length} products`;

    if (!products.length) {
      container.innerHTML = '<div class="empty-state">No products registered. Create one to begin.</div>';
      select.innerHTML = '<option value="">No products available</option>';
      return;
    }

    // Preserve selected value if possible
    const prevSelected = select.value;
    select.innerHTML = '';

    container.innerHTML = products.map(p => {
      const pct = p.initial_stock > 0 ? Math.min(100, Math.round((p.available_stock / p.initial_stock) * 100)) : 0;
      
      // Populate select option
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.innerText = `${p.name} (Available: ${p.available_stock}/${p.initial_stock})`;
      select.appendChild(opt);

      return `
        <div class="product-row" data-id="${p.id}">
          <div class="product-info">
            <div class="product-name-bar">
              <span class="product-title">${p.name}</span>
              <span class="badge ${p.available_stock > 0 ? 'badge-success' : 'badge-danger'}">
                ${p.available_stock} Avail
              </span>
            </div>
            <div class="product-desc">${p.description || 'No description provided.'}</div>
            <div class="stock-meter">
              <div class="stock-meter-fill" style="width: ${pct}%"></div>
            </div>
            <div class="product-stats-line">
              <span>Initial: <strong>${p.initial_stock}</strong></span>
              <span>Active Holds: <strong class="text-amber">${p.active_holds}</strong></span>
              <span>Confirmed Sold: <strong class="text-emerald">${p.confirmed_units}</strong></span>
            </div>
          </div>
          <div class="product-actions">
            <button class="btn btn-secondary btn-xs btn-quick-select" data-id="${p.id}">Reserve</button>
            <button class="btn btn-secondary btn-xs btn-quick-reset" data-id="${p.id}" title="Reset Stock">Reset</button>
          </div>
        </div>
      `;
    }).join('');

    if (prevSelected && products.some(p => p.id === prevSelected)) {
      select.value = prevSelected;
    }

    // Attach row button handlers
    document.querySelectorAll('.btn-quick-select').forEach(btn => {
      btn.addEventListener('click', (e) => {
        select.value = e.target.getAttribute('data-id');
        document.getElementById('res-quantity').focus();
      });
    });

    document.querySelectorAll('.btn-quick-reset').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        const pid = e.target.getAttribute('data-id');
        try {
          await fetch(`/products/${pid}/reset`, { method: 'POST' });
          showToast('Product inventory reset successfully', 'success');
          refreshDashboard();
        } catch (err) {
          showToast('Failed to reset product inventory', 'error');
        }
      });
    });

  } catch (err) {
    console.error('Failed to load products', err);
  }
}

// 3. Analytics & Overview
async function loadAnalytics() {
  try {
    const res = await fetch('/analytics/overview');
    const data = await res.json();

    document.getElementById('metric-total-products').innerText = data.total_products;
    document.getElementById('metric-active-holds').innerText = data.active_holds;
    document.getElementById('metric-confirmed-bookings').innerText = data.confirmed_bookings;
    document.getElementById('metric-expired-bookings').innerText = data.expired_bookings + data.cancelled_bookings;

    const badge = document.getElementById('metric-invariant-badge');
    if (data.inventory_invariant_satisfied) {
      badge.innerHTML = '<span class="badge badge-success">SATISFIED</span>';
    } else {
      badge.innerHTML = '<span class="badge badge-danger">VIOLATED</span>';
    }

    // Aggregation 1: Bookings by Status
    const statusRes = await fetch('/analytics/bookings-by-status');
    const statusList = await statusRes.json();
    const statusTbody = document.getElementById('tbl-bookings-status');
    if (statusList.length) {
      statusTbody.innerHTML = statusList.map(s => `
        <tr>
          <td><span class="badge badge-${s.status}">${s.status}</span></td>
          <td><strong>${s.count}</strong></td>
          <td>${s.total_quantity}</td>
        </tr>
      `).join('');
    } else {
      statusTbody.innerHTML = '<tr><td colspan="3" class="text-subtle text-center">No bookings recorded yet.</td></tr>';
    }

    // Aggregation 2: Product Performance
    const perfRes = await fetch('/analytics/product-performance');
    const perfList = await perfRes.json();
    const perfTbody = document.getElementById('tbl-product-perf');
    if (perfList.length) {
      perfTbody.innerHTML = perfList.map(p => `
        <tr>
          <td><strong>${p.product_name}</strong></td>
          <td>${p.available_stock} / ${p.initial_stock}</td>
          <td class="text-amber">${p.active_holds}</td>
          <td class="text-emerald">${p.confirmed_count} (${p.total_units_sold} units)</td>
          <td class="text-purple">${p.expired_count}</td>
        </tr>
      `).join('');
    } else {
      perfTbody.innerHTML = '<tr><td colspan="5" class="text-subtle text-center">No performance data yet.</td></tr>';
    }

    // MongoDB Indexes Inspection
    const idxRes = await fetch('/analytics/indexes');
    const idxData = await idxRes.json();
    const idxContainer = document.getElementById('index-list-container');
    idxContainer.innerHTML = Object.entries(idxData).map(([coll, idxs]) => `
      <div style="margin-bottom: 0.6rem;">
        <strong style="color: var(--accent-cyan); font-size: 0.8rem;">Collection: ${coll}</strong>
        <ul style="padding-left: 1.2rem; font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-muted); margin-top: 0.2rem;">
          ${Object.entries(idxs).map(([name, def]) => `
            <li><strong>${name}</strong>: ${JSON.stringify(def.key)} ${def.unique ? '(UNIQUE)' : ''}</li>
          `).join('')}
        </ul>
      </div>
    `).join('');

  } catch (err) {
    console.error('Failed to load analytics', err);
  }
}

// 4. Recent Bookings Audit Table
async function loadRecentBookings() {
  try {
    const res = await fetch('/bookings?limit=15');
    const bookings = await res.json();
    const tbody = document.getElementById('tbl-bookings-recent');

    if (!bookings.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-subtle text-center">No bookings found.</td></tr>';
      return;
    }

    tbody.innerHTML = bookings.map(b => `
      <tr>
        <td class="font-mono text-cyan" title="${b.id}">${b.id.substring(0, 8)}...</td>
        <td class="font-mono text-xs">${b.product_id.substring(0, 12)}</td>
        <td><strong>${b.quantity}</strong></td>
        <td><span class="badge badge-${b.status}">${b.status}</span></td>
        <td class="text-subtle">${formatTime(b.created_at)}</td>
      </tr>
    `).join('');
  } catch (err) {
    console.error('Failed to load recent bookings', err);
  }
}

// 5. Connect SSE Contention Stream
function connectStream(replay = true) {
  if (eventSource) {
    eventSource.close();
  }

  const sseChip = document.getElementById('sse-status-chip');
  const sseText = document.getElementById('sse-status-text');
  sseChip.className = 'status-chip live-chip';
  sseText.innerText = 'Connecting...';

  const url = `/events/stream?replay=${replay}`;
  eventSource = new EventSource(url);

  eventSource.onopen = () => {
    sseChip.className = 'status-chip live-chip up';
    sseText.innerText = 'Live Feed';
  };

  eventSource.onerror = (err) => {
    console.warn('SSE stream error, re-establishing...', err);
    sseChip.className = 'status-chip down';
    sseText.innerText = 'Reconnecting...';
  };

  // Generic message handler
  eventSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      appendStreamEvent(e.type || 'LIFECYCLE', data);
    } catch (err) {
      console.warn('Failed to parse SSE payload', err);
    }
  };

  // Specific event types dispatched by Redis Streams
  const eventTypes = [
    'HOLD_CREATED',
    'BOOKING_CONFIRMED',
    'HOLD_EXPIRED',
    'HOLD_CANCELLED',
    'RESERVATION_REJECTED'
  ];

  eventTypes.forEach(type => {
    eventSource.addEventListener(type, (e) => {
      try {
        const data = JSON.parse(e.data);
        appendStreamEvent(type, data);
        // Refresh products and metrics on live events
        loadProducts();
        loadAnalytics();
        loadRecentBookings();
      } catch (err) {
        console.warn(`Error handling event ${type}:`, err);
      }
    });
  });
}

function appendStreamEvent(type, data) {
  const container = document.getElementById('stream-feed-container');
  const item = document.createElement('div');
  item.className = `stream-event-item event-${type}`;

  const timeStr = formatTime(data.timestamp || new Date().toISOString());
  let summary = '';

  if (type === 'HOLD_CREATED') {
    summary = `Hold acquired for ${data.quantity || 1} unit(s) [Booking: ${data.booking_id ? data.booking_id.substring(0, 8) : 'N/A'}]`;
  } else if (type === 'BOOKING_CONFIRMED') {
    summary = `Booking CONFIRMED for ${data.quantity || 1} unit(s) [Booking: ${data.booking_id ? data.booking_id.substring(0, 8) : 'N/A'}]`;
  } else if (type === 'HOLD_EXPIRED') {
    summary = `Hold EXPIRED. Inventory returned to stock (${data.quantity || 1} units).`;
  } else if (type === 'HOLD_CANCELLED') {
    summary = `Hold CANCELLED. Inventory returned to available stock.`;
  } else if (type === 'RESERVATION_REJECTED') {
    summary = `Reservation REJECTED: ${data.outcome || 'Insufficient stock'}`;
  } else {
    summary = data.message || JSON.stringify(data);
  }

  item.innerHTML = `
    <span class="event-time">${timeStr}</span>
    <span class="event-badge badge-${type}">${type}</span>
    <span class="event-summary">${summary}</span>
  `;

  // Prepend to top of feed
  container.insertBefore(item, container.firstChild);

  // Keep feed max 100 items to avoid DOM bloat
  if (container.children.length > 100) {
    container.removeChild(container.lastChild);
  }
}

// 6. Reservation Form Submission
document.getElementById('reservation-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const productId = document.getElementById('res-product-select').value;
  const quantity = parseInt(document.getElementById('res-quantity').value, 10);
  const ttl = parseInt(document.getElementById('res-ttl').value, 10);
  const idempKey = document.getElementById('res-idempotency-key').value;

  if (!productId) {
    showToast('Please select a product first', 'error');
    return;
  }

  const submitBtn = document.getElementById('btn-submit-reserve');
  submitBtn.disabled = true;
  submitBtn.innerText = 'Reserving...';

  try {
    const res = await fetch('/reservations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        product_id: productId,
        quantity: quantity,
        hold_ttl_seconds: ttl,
        idempotency_key: idempKey
      })
    });

    const data = await res.json();

    if (!res.ok) {
      showToast(data.detail || 'Reservation rejected', 'error');
      // Generate new idempotency key for next attempt
      document.getElementById('res-idempotency-key').value = generateUUID();
      return;
    }

    showToast(`Hold created: ${data.booking_id.substring(0, 8)}...`, 'success');
    setupActiveHold(data, ttl);
    refreshDashboard();

    // Prepare next idempotency key
    document.getElementById('res-idempotency-key').value = generateUUID();

  } catch (err) {
    showToast(`Network error: ${err.message}`, 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerText = 'Acquire Temporary Hold';
  }
});

// 7. Active Hold Countdown & Actions
function setupActiveHold(bookingData, ttlSeconds) {
  if (activeHoldTimer) {
    clearInterval(activeHoldTimer);
  }

  currentActiveHold = bookingData;
  const panel = document.getElementById('active-hold-container');
  const timerElem = document.getElementById('hold-countdown');

  document.getElementById('hold-booking-id').innerText = bookingData.booking_id;
  document.getElementById('hold-units').innerText = bookingData.quantity;
  document.getElementById('hold-product-name').innerText = bookingData.product_id.substring(0, 16);
  document.getElementById('hold-expires-at').innerText = formatTime(bookingData.expires_at);

  panel.style.display = 'block';

  let remaining = ttlSeconds;
  timerElem.innerText = `${remaining}s remaining`;

  activeHoldTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(activeHoldTimer);
      timerElem.innerText = 'Hold Expired';
      setTimeout(() => {
        panel.style.display = 'none';
        currentActiveHold = null;
        refreshDashboard();
      }, 2000);
    } else {
      timerElem.innerText = `${remaining}s remaining`;
    }
  }, 1000);
}

document.getElementById('btn-confirm-hold').addEventListener('click', async () => {
  if (!currentActiveHold) return;
  const bookingId = currentActiveHold.booking_id;

  try {
    const res = await fetch(`/reservations/${bookingId}/confirm`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast('Booking successfully CONFIRMED!', 'success');
      clearInterval(activeHoldTimer);
      document.getElementById('active-hold-container').style.display = 'none';
      currentActiveHold = null;
      refreshDashboard();
    } else {
      showToast(data.detail || 'Confirmation failed', 'error');
    }
  } catch (err) {
    showToast('Failed to confirm booking', 'error');
  }
});

document.getElementById('btn-cancel-hold').addEventListener('click', async () => {
  if (!currentActiveHold) return;
  const bookingId = currentActiveHold.booking_id;

  try {
    const res = await fetch(`/reservations/${bookingId}/cancel`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast('Booking CANCELLED. Inventory released.', 'info');
      clearInterval(activeHoldTimer);
      document.getElementById('active-hold-container').style.display = 'none';
      currentActiveHold = null;
      refreshDashboard();
    } else {
      showToast(data.detail || 'Cancellation failed', 'error');
    }
  } catch (err) {
    showToast('Failed to cancel booking', 'error');
  }
});

// 8. 100-Client High-Contention Race Simulator
document.getElementById('btn-run-stress-test').addEventListener('click', async () => {
  const btn = document.getElementById('btn-run-stress-test');
  const resultBox = document.getElementById('stress-result-box');

  btn.disabled = true;
  btn.innerText = 'Running 100 Requests...';
  resultBox.style.display = 'block';
  resultBox.innerHTML = 'Executing 100 concurrent reservation requests against product with 10 units...';

  // Target product: RTX 4090 (or first product)
  const products = await (await fetch('/products')).json();
  const target = products.find(p => p.id === 'prod-rtx-4090') || products[0];

  if (!target) {
    resultBox.innerHTML = 'Error: No target product found.';
    btn.disabled = false;
    btn.innerText = '🚀 Run 100-Client Race';
    return;
  }

  // Reset target product inventory to exactly 10 first
  await fetch(`/products/${target.id}/reset?stock=10`, { method: 'POST' });

  const totalClients = 100;
  const startTime = performance.now();

  const requests = Array.from({ length: totalClients }, (_, i) => {
    const key = `race-test-${Date.now()}-${i}`;
    return fetch('/reservations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        product_id: target.id,
        quantity: 1,
        hold_ttl_seconds: 20,
        idempotency_key: key
      })
    }).then(r => r.status);
  });

  const responses = await Promise.all(requests);
  const durationMs = Math.round(performance.now() - startTime);

  let successCount = 0;
  let rejectedCount = 0;
  let otherCount = 0;

  responses.forEach(code => {
    if (code === 200 || code === 201) successCount++;
    else if (code === 400) rejectedCount++;
    else otherCount++;
  });

  const updatedTarget = await (await fetch(`/products/${target.id}`)).json();
  const invariantHold = (successCount <= 10) && (updatedTarget.available_stock >= 0);

  resultBox.innerHTML = `
    <strong>CONCURRENCY BENCHMARK RESULTS</strong><br>
    &bull; Total Concurrent Requests: <strong>${totalClients}</strong><br>
    &bull; Initial Stock: <strong>10 units</strong><br>
    &bull; Successfully Reserved (201): <strong class="text-emerald">${successCount}</strong><br>
    &bull; Correctly Rejected (400): <strong class="text-rose">${rejectedCount}</strong><br>
    &bull; Final Redis Available Stock: <strong>${updatedTarget.available_stock}</strong><br>
    &bull; Zero-Oversell Invariant: <strong class="${invariantHold ? 'text-emerald' : 'text-rose'}">
      ${invariantHold ? 'PASS (Zero Overselling)' : 'FAIL (Oversold!)'}
    </strong><br>
    &bull; Total Duration: <strong>${durationMs}ms</strong> (~${Math.round((totalClients / durationMs) * 1000)} req/s)
  `;

  btn.disabled = false;
  btn.innerText = '🚀 Run 100-Client Race';
  refreshDashboard();
});

// 9. Create Product Modal
const createModal = document.getElementById('modal-create-product');
document.getElementById('btn-open-create-modal').addEventListener('click', () => {
  createModal.classList.add('active');
});
document.getElementById('btn-close-product-modal').addEventListener('click', () => {
  createModal.classList.remove('active');
});
document.getElementById('btn-cancel-product-modal').addEventListener('click', () => {
  createModal.classList.remove('active');
});

document.getElementById('form-create-product').addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = document.getElementById('new-prod-name').value;
  const stock = parseInt(document.getElementById('new-prod-stock').value, 10);
  const desc = document.getElementById('new-prod-desc').value;

  try {
    const res = await fetch('/products', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, initial_stock: stock, description: desc })
    });
    if (res.ok) {
      showToast(`Product '${name}' created successfully`, 'success');
      createModal.classList.remove('active');
      document.getElementById('form-create-product').reset();
      refreshDashboard();
    } else {
      const err = await res.json();
      showToast(err.detail || 'Failed to create product', 'error');
    }
  } catch (err) {
    showToast('Network error creating product', 'error');
  }
});

// 10. Reset Demo Data
document.getElementById('btn-reset-db').addEventListener('click', async () => {
  if (!confirm('Reset database and restore default test seed data?')) return;
  try {
    // Re-trigger seed via products reset or refresh
    const products = await (await fetch('/products')).json();
    for (const p of products) {
      await fetch(`/products/${p.id}/reset`, { method: 'POST' });
    }
    showToast('Demo inventory reset to baseline', 'success');
    refreshDashboard();
  } catch (err) {
    showToast('Failed to reset demo data', 'error');
  }
});

// 11. Tabs
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    btn.classList.add('active');
    const targetId = btn.getAttribute('data-tab');
    document.getElementById(targetId).classList.add('active');
  });
});

// 12. Regenerate idempotency key
document.getElementById('btn-regen-key').addEventListener('click', () => {
  document.getElementById('res-idempotency-key').value = generateUUID();
});

// Clear Feed & Replay Buttons
document.getElementById('btn-clear-feed').addEventListener('click', () => {
  document.getElementById('stream-feed-container').innerHTML = '';
});

document.getElementById('btn-replay-stream').addEventListener('click', () => {
  document.getElementById('stream-feed-container').innerHTML = '';
  connectStream(true);
});

document.getElementById('btn-refresh-all').addEventListener('click', () => {
  refreshDashboard();
  showToast('Dashboard refreshed', 'info');
});

function refreshDashboard() {
  checkHealth();
  loadProducts();
  loadAnalytics();
  loadRecentBookings();
}

// Initialization on DOM load
window.addEventListener('DOMContentLoaded', () => {
  document.getElementById('res-idempotency-key').value = generateUUID();
  refreshDashboard();
  connectStream(true);

  // Periodic polling every 5 seconds to keep health and timers fresh
  setInterval(() => {
    checkHealth();
  }, 5000);
});
