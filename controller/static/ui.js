/* sonos-box config UI */
let activeItems = [];
let availableItems = [];
let sonosById = {};
let dragIdx = null;
let playingId = null;
let roomPresets = [];
let activeRoomPreset = null;
let discoveredRooms = [];

function mergeSonosMeta(items) {
  return items.map(item => {
    const meta = sonosById[item.id];
    if (!meta) return item;
    return { ...item, name: item.name || meta.name, description: meta.description };
  });
}

function itemTitle(item) {
  return item.label || item.name || item.id;
}

function itemSubtitle(item) {
  if (item.label) return item.description || '';
  return item.description || '';
}

function itemNameHtml(item, idx, editable) {
  const title = itemTitle(item);
  const sub = itemSubtitle(item);
  if (editable) {
    return `<span class="item-name">
      <input id="name-${idx}" value="${escHtml(title)}" title="click ✎ to rename" readonly
        onblur="commitRename(${idx}, this)"
        onkeydown="if(event.key==='Enter'){this.blur()} if(event.key==='Escape'){this.value=itemTitle(activeItems[${idx}]);this.blur()}"
      />
      ${sub ? `<div class="item-desc">${escHtml(sub)}</div>` : ''}
    </span>`;
  }
  return `<span class="item-name">
    <div class="item-title">${escHtml(title)}</div>
    ${sub ? `<div class="item-desc">${escHtml(sub)}</div>` : ''}
  </span>`;
}

async function load() {
  try {
    const [configRes, browseRes, roomsRes, presetsRes] = await Promise.all([
      fetch('/items').then(r => { if (!r.ok) throw new Error('items'); return r.json(); }),
      fetch('/browse').then(r => { if (!r.ok) throw new Error('browse'); return r.json(); }),
      fetch('/rooms').then(r => r.json()).catch(() => ({ rooms: [] })),
      fetch('/room_presets').then(r => r.json()).catch(() => ({ room_presets: [], active_room_preset: null })),
    ]);

    sonosById = {};
    for (const item of [...(browseRes.favorites || []), ...(browseRes.playlists || [])]) {
      sonosById[item.id] = item;
    }

    activeItems = mergeSonosMeta(configRes.items || []);

    const activeIds = new Set(activeItems.map(i => i.id));
    const allAvailable = [
      ...(browseRes.favorites || []),
      ...(browseRes.playlists || []),
    ];
    availableItems = allAvailable.filter(i => !activeIds.has(i.id));

    discoveredRooms = roomsRes.rooms || [];
    roomPresets = presetsRes.room_presets || configRes.room_presets || [];
    activeRoomPreset = presetsRes.active_room_preset ?? configRes.active_room_preset ?? null;
  } catch (e) {
    const status = document.getElementById('status');
    if (status) {
      status.className = 'status err';
      status.textContent = 'Can’t reach API';
    }
  }

  renderActive();
  renderAvailable();
  renderRooms();
}

function renderActive() {
  const el = document.getElementById('active-list');
  if (!activeItems.length) {
    el.innerHTML = '<div class="empty">Empty — add from Available</div>';
    return;
  }
  el.innerHTML = '';
  activeItems.forEach((item, idx) => {
    const div = document.createElement('div');
    div.className = 'item';
    div.draggable = true;
    div.dataset.idx = idx;

    const badgeClass = item.type === 'playlist' ? 'badge-pl' : 'badge-fav';
    const badgeText = item.type === 'playlist' ? 'playlist' : 'favorite';

    const playing = playingId === item.id ? ' playing' : '';
    div.innerHTML = `
      <span class="handle" title="drag to reorder">⠿</span>
      ${itemNameHtml(item, idx, true)}
      <button class="icon-btn play${playing}" title="play on Sonos" onclick="playItem(${idx}, true)">▶</button>
      <button class="icon-btn edit" title="rename" onclick="startRename(${idx})">✎</button>
      <span class="badge ${badgeClass}">${badgeText}</span>
      <button class="icon-btn rm" title="remove" onclick="removeItem(${idx})">×</button>
    `;

    div.addEventListener('dragstart', () => { dragIdx = idx; div.classList.add('dragging'); });
    div.addEventListener('dragend',   () => { dragIdx = null; div.classList.remove('dragging'); renderActive(); });
    div.addEventListener('dragover',  e => { e.preventDefault(); div.classList.add('drag-over'); });
    div.addEventListener('dragleave', () => div.classList.remove('drag-over'));
    div.addEventListener('drop', e => {
      e.preventDefault();
      div.classList.remove('drag-over');
      if (dragIdx === null || dragIdx === idx) return;
      const moved = activeItems.splice(dragIdx, 1)[0];
      activeItems.splice(idx, 0, moved);
      dragIdx = null;
      renderActive();
    });

    el.appendChild(div);
  });
}

function renderAvailable() {
  const el = document.getElementById('available-list');
  if (!availableItems.length) {
    el.innerHTML = activeItems.length
      ? '<div class="empty">Nothing left to add</div>'
      : '<div class="empty">No playlists or favorites from Sonos</div>';
    return;
  }
  el.innerHTML = '';
  availableItems.forEach((item, idx) => {
    const div = document.createElement('div');
    div.className = 'item';
    const badgeClass = item.type === 'playlist' ? 'badge-pl' : 'badge-fav';
    const badgeText = item.type === 'playlist' ? 'playlist' : 'favorite';
    const playing = playingId === item.id ? ' playing' : '';
    div.innerHTML = `
      ${itemNameHtml(item, idx, false)}
      <button class="icon-btn play${playing}" title="play on Sonos" onclick="playItem(${idx}, false)">▶</button>
      <span class="badge ${badgeClass}">${badgeText}</span>
      <button class="icon-btn rm" style="color:var(--lime);font-size:18px" title="add to device" onclick="addItem(${idx})">+</button>
    `;
    el.appendChild(div);
  });
}

function addItem(idx) {
  const item = availableItems.splice(idx, 1)[0];
  activeItems.push({ type: item.type, id: item.id, name: item.name, description: item.description });
  renderActive();
  renderAvailable();
}

function removeItem(idx) {
  const item = activeItems.splice(idx, 1)[0];
  availableItems.unshift({
    type: item.type,
    id: item.id,
    name: item.name || item.label || item.id,
    description: item.description,
  });
  renderActive();
  renderAvailable();
}

function startRename(idx) {
  const input = document.getElementById(`name-${idx}`);
  if (!input) return;
  input.removeAttribute('readonly');
  input.focus();
  input.select();
}

function commitRename(idx, input) {
  input.setAttribute('readonly', true);
  activeItems[idx].label = input.value.trim() || activeItems[idx].label;
}

async function playItem(idx, onDevice) {
  const item = onDevice ? activeItems[idx] : availableItems[idx];
  const status = document.getElementById('status');
  status.className = 'status';
  status.textContent = `Playing ${itemTitle(item)}…`;
  playingId = item.id;
  renderActive();
  renderAvailable();
  try {
    const res = await fetch('/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: item.type, id: item.id }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || 'Play failed');
    }
    if (data.artist) {
      item.description = data.artist;
      sonosById[item.id] = { ...sonosById[item.id], description: data.artist };
    }
    status.className = 'status ok';
    status.textContent = `Playing on Sonos: ${itemTitle(item)}`;
    renderActive();
    renderAvailable();
  } catch (e) {
    playingId = null;
    renderActive();
    renderAvailable();
    status.className = 'status err';
    status.textContent = `Play failed: ${e.message}`;
  }
}

async function save() {
  const status = document.getElementById('status');
  status.className = 'status';
  status.textContent = 'Saving...';
  try {
    const payload = activeItems.map(({ type, id, label }) => {
      const item = { type, id };
      if (label) item.label = label;
      return item;
    });
    const res = await fetch('/items', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: payload }),
    });
    const data = await res.json();
    if (res.ok) {
      await fetch('/reload', { method: 'POST' });
      status.className = 'status ok';
      status.textContent = `Saved ${data.count} item${data.count !== 1 ? 's' : ''} — device updated`;
    } else {
      throw new Error(data.description || 'Unknown error');
    }
  } catch(e) {
    status.className = 'status err';
    status.textContent = `Error: ${e.message}`;
  }
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function slugId(label) {
  return (label || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'preset';
}

function makeChip(name, selected, idx, missing) {
  const span = document.createElement('span');
  span.className = 'chip' + (selected ? ' on' : '') + (missing ? ' missing' : '');
  span.textContent = name;
  span.title = missing
    ? 'Not found on Sonos — click to remove'
    : (selected ? 'Selected — click to remove' : 'Click to add to this preset');
  span.addEventListener('click', () => toggleRoom(idx, name));
  return span;
}

function renderRooms() {
  const el = document.getElementById('room-presets');
  if (!roomPresets.length) {
    el.innerHTML = '<div class="empty">No presets yet</div>';
    return;
  }
  const known = new Set(discoveredRooms.map(r => r.name));
  el.innerHTML = '';
  roomPresets.forEach((preset, idx) => {
    const div = document.createElement('div');
    div.className = 'room-row' + (preset.id === activeRoomPreset ? ' active' : '');
    const rooms = preset.rooms || [];

    const label = document.createElement('input');
    label.type = 'text';
    label.value = preset.label || '';
    label.placeholder = 'Label';
    label.addEventListener('change', () => {
      roomPresets[idx].label = label.value.trim();
      if (!roomPresets[idx].id) roomPresets[idx].id = slugId(label.value);
    });

    const chips = document.createElement('div');
    chips.className = 'room-chips';
    if (!discoveredRooms.length) {
      const empty = document.createElement('span');
      empty.className = 'empty';
      empty.textContent = 'No rooms discovered — is Sonos reachable?';
      chips.appendChild(empty);
    } else {
      for (const r of discoveredRooms) {
        chips.appendChild(makeChip(r.name, rooms.includes(r.name), idx, false));
      }
    }
    for (const n of rooms.filter(name => !known.has(name))) {
      chips.appendChild(makeChip(n, true, idx, true));
    }

    const actions = document.createElement('div');
    actions.className = 'room-actions';
    const useBtn = document.createElement('button');
    useBtn.className = 'secondary';
    useBtn.textContent = preset.id === activeRoomPreset ? 'active' : 'use';
    useBtn.title = 'apply on device';
    useBtn.addEventListener('click', () => activatePreset(idx));
    const rmBtn = document.createElement('button');
    rmBtn.className = 'icon-btn rm';
    rmBtn.title = 'remove';
    rmBtn.textContent = '×';
    rmBtn.addEventListener('click', () => removePreset(idx));
    actions.append(useBtn, rmBtn);

    div.append(label, chips, actions);
    el.appendChild(div);
  });
}

function toggleRoom(idx, name) {
  const rooms = roomPresets[idx].rooms || (roomPresets[idx].rooms = []);
  const i = rooms.indexOf(name);
  if (i >= 0) rooms.splice(i, 1);
  else rooms.push(name);
  renderRooms();
}

function addPreset() {
  let n = roomPresets.length + 1;
  let id = `preset-${n}`;
  while (roomPresets.some(p => p.id === id)) { n += 1; id = `preset-${n}`; }
  roomPresets.push({ id, label: `Preset ${n}`, rooms: [] });
  renderRooms();
}

function removePreset(idx) {
  const removed = roomPresets.splice(idx, 1)[0];
  if (removed && removed.id === activeRoomPreset) activeRoomPreset = null;
  renderRooms();
}

async function activatePreset(idx) {
  const preset = roomPresets[idx];
  const status = document.getElementById('rooms-status');
  status.className = 'status';
  status.textContent = 'Saving & applying…';
  try {
    await saveRooms(false);
    const res = await fetch('/active_room_preset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: preset.id }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.description || data.error || 'Apply failed');
    activeRoomPreset = preset.id;
    renderRooms();
    status.className = 'status ok';
    status.textContent = `Active: ${preset.label}`;
  } catch (e) {
    status.className = 'status err';
    status.textContent = `Error: ${e.message}`;
  }
}

async function saveRooms(showOk = true) {
  const status = document.getElementById('rooms-status');
  status.className = 'status';
  status.textContent = 'Saving rooms…';
  try {
    const payload = roomPresets.map(p => ({
      id: p.id || slugId(p.label),
      label: (p.label || '').trim() || p.id,
      rooms: p.rooms || [],
    }));
    const res = await fetch('/room_presets', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_presets: payload }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.description || 'Unknown error');
    roomPresets = data.room_presets || payload;
    if (data.active_room_preset !== undefined) activeRoomPreset = data.active_room_preset;
    await fetch('/reload', { method: 'POST' });
    renderRooms();
    if (showOk) {
      status.className = 'status ok';
      status.textContent = `Saved ${data.count} preset${data.count !== 1 ? 's' : ''}`;
    }
  } catch (e) {
    status.className = 'status err';
    status.textContent = `Error: ${e.message}`;
    throw e;
  }
}

load();
