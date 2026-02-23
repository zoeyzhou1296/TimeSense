// TimeSense v2 - Week-focused time tracking
console.log("TimeSense app.js loaded (build " + (typeof window !== "undefined" && window.__TIMESENSE_BUILD__ ? window.__TIMESENSE_BUILD__ : "?") + ")");
let allCategories = [];
let currentWeekStart = null;
let hideNight = true; // DEFAULT: hide 12-6am
let selectedEntry = null;
let selectedPlannedEvent = null;
// Undo stack: last action for Ctrl+Z / Cmd+Z
let undoStack = null; // { type: 'create'|'delete', entry: {...}, createdId?: string }
// After drag-to-create: time chunk waiting for user to pick a quick-log category (or type title and click +)
let pendingQuickLogChunk = null; // { start_at: ISO string, end_at: ISO string }

// Google Calendar–style pastel colors (light backgrounds, dark text for readability)
const CHART_COLORS = {
  "Work": "#a8c7e0",
  "Work (active)": "#7986cb",
  "Work (passive)": "#9fa8da",
  "Learning": "#80cbc4",
  "Exercise": "#a5d6a7",
  "Intimate / Quality Time": "#f48fb1",
  "Intimacy / quality time": "#f48fb1",
  "Sleep": "#b0bec5",
  "Social": "#ffe082",
  "Appointment": "#ffe082",
  "Entertainment": "#90caf9",
  "Commute": "#bcaaa4",
  "Chores": "#ce93d8",
  "Life essentials": "#81d4fa",
  "Unplanned / Wasted": "#ef9a9a",
  "Unplanned wasting": "#ef9a9a",
  "Other": "#cfd8dc",
};

function getCategoryColor(categoryName) {
  if (!categoryName) return "#cfd8dc";
  const n = (categoryName || "").trim();
  if (CHART_COLORS[n]) return CHART_COLORS[n];
  const lower = n.toLowerCase();
  const key = Object.keys(CHART_COLORS).find(k => k.toLowerCase() === lower);
  if (key) return CHART_COLORS[key];
  if (lower.includes("intim") || lower.includes("quality")) return CHART_COLORS["Intimacy / quality time"];
  if (lower.includes("work")) return CHART_COLORS["Work (active)"];
  if (lower.includes("unplanned") || lower.includes("wast")) return CHART_COLORS["Unplanned / Wasted"];
  return CHART_COLORS["Other"] || "#cfd8dc";
}

// Same as calendar for sidebar panels (Weekly Targets, This Week, Insights modal).
// Uses category color from API (allCategories) when set; else CHART_COLORS / canonical aliases.
function getCategoryColorForPanel(categoryName) {
  if (!categoryName) return "#cfd8dc";
  const n = (categoryName || "").trim();
  const cat = allCategories.find(c => (c.name || "").trim() === n || (c.name || "").toLowerCase() === n.toLowerCase());
  if (cat && cat.color) return cat.color;
  const lower = n.toLowerCase();
  // Canonical aliases (backend/targets use "Work", "Intimate / Quality Time", etc. — map to calendar keys)
  if (lower === "work") return CHART_COLORS["Work (active)"];
  if (lower === "intimate / quality time" || lower === "intimacy / quality time") return CHART_COLORS["Intimacy / quality time"];
  if (lower === "unplanned / wasted" || lower === "unplanned wasting") return CHART_COLORS["Unplanned / Wasted"];
  if (lower.includes("intim") || lower.includes("quality")) return CHART_COLORS["Intimacy / quality time"];
  if (lower.includes("work")) return CHART_COLORS["Work (active)"];
  if (lower.includes("unplanned") || lower.includes("wast")) return CHART_COLORS["Unplanned / Wasted"];
  if (lower.includes("social") || lower.includes("appointment")) return CHART_COLORS["Social"];
  if (lower.includes("sleep")) return CHART_COLORS["Sleep"];
  if (lower.includes("learn")) return CHART_COLORS["Learning"];
  if (lower.includes("exercise")) return CHART_COLORS["Exercise"];
  if (lower.includes("life") || lower.includes("essential")) return CHART_COLORS["Life essentials"];
  if (lower.includes("commute")) return CHART_COLORS["Commute"];
  if (lower.includes("chore")) return CHART_COLORS["Chores"];
  if (lower.includes("entertainment")) return CHART_COLORS["Entertainment"];
  // Exact match
  if (CHART_COLORS[n]) return CHART_COLORS[n];
  // Case-insensitive match against CHART_COLORS keys
  const exactKey = Object.keys(CHART_COLORS).find((k) => k.toLowerCase() === lower);
  if (exactKey) return CHART_COLORS[exactKey];
  return CHART_COLORS["Other"] || "#cfd8dc";
}

// Increase saturation (reduce transparency feeling) for stats colors
function getCategoryColorForStats(categoryName) {
  const baseColor = getCategoryColor(categoryName);
  // Convert hex to RGB, increase saturation, convert back
  const hex = baseColor.replace('#', '');
  const r = parseInt(hex.substr(0, 2), 16);
  const g = parseInt(hex.substr(2, 2), 16);
  const b = parseInt(hex.substr(4, 2), 16);
  
  // Increase saturation by making colors more vibrant (reduce greyness)
  // Formula: new = old + (255 - old) * 0.15 (increase brightness/saturation by 15%)
  const boost = 0.15;
  const newR = Math.min(255, Math.round(r + (255 - r) * boost));
  const newG = Math.min(255, Math.round(g + (255 - g) * boost));
  const newB = Math.min(255, Math.round(b + (255 - b) * boost));
  
  return `rgb(${newR}, ${newG}, ${newB})`;
}

function $(id) { return document.getElementById(id); }
function setStatus(msg) { if ($("status")) $("status").textContent = msg; }

async function apiGet(path, fetchOptions = {}) {
  const res = await fetch(path, {
    headers: { Accept: "application/json", ...fetchOptions.headers },
    ...fetchOptions,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

async function apiPatch(path, body) {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

// ─────────────────────────────────────────────────────────────
// Date Helpers
// ─────────────────────────────────────────────────────────────
// Week starts Sunday 00:00 (system time). Returns YYYY-MM-DD of the Sunday starting the week containing d.
function getSunday(d) {
  const date = new Date(d);
  const day = date.getDay(); // 0=Sun, 1=Mon, ..., 6=Sat
  date.setDate(date.getDate() - day);
  return formatDate(date);
}

function formatDate(d) {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function addDays(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00`);
  d.setDate(d.getDate() + days);
  return formatDate(d);
}

function fmtTime(d) {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function minsBetween(a, b) {
  return Math.max(0, Math.round((b.getTime() - a.getTime()) / 60000));
}

function getVisibleHourRange() {
  return hideNight ? [6, 24] : [0, 24];
}

// Calendar zoom: more pixels per hour = easier to draw accurate chunks (default 90)
let PIXELS_PER_HOUR = 90;
function setCalendarZoom(pxPerHour) {
  PIXELS_PER_HOUR = Math.max(45, Math.min(120, pxPerHour));
  if (currentWeekStart) refreshWeek();
}
function getPixelsPerHour() { return PIXELS_PER_HOUR; }

// ─────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────
async function init() {
  setStatus("Loading...");
  // Restore calendar toggles from localStorage (default: only Apple + imported; Google and Outlook off)
  const toggleGoogle = document.getElementById("toggle-include-google");
  if (toggleGoogle) toggleGoogle.checked = localStorage.getItem("timesense_include_google") === "1";
  const toggleOutlook = document.getElementById("toggle-include-outlook");
  if (toggleOutlook) toggleOutlook.checked = localStorage.getItem("timesense_include_outlook") === "1";
  const aiLang = document.getElementById("ai-lang");
  if (aiLang) aiLang.value = localStorage.getItem("timesense_ai_lang") || "en";

  try {
    allCategories = await apiGet("/api/categories");
  } catch { allCategories = []; }
  populateTargetCategoryDropdown();

  currentWeekStart = getSunday(new Date());
  
  await renderQuickButtons();
  await refreshWeek();
  await refreshAnalytics();
  await refreshTargetsProgress();
  await refreshGoals();
  await refreshUncategorized();
  await refreshConnections();
  setupAppleIcsImport();
  
  setStatus("Ready");
  setupEventListeners();
}

function setupEventListeners() {
  // Week navigation
  $("btn-prev-week").onclick = () => { 
    currentWeekStart = addDays(currentWeekStart, -7); 
    setStatus("Loading week...");
    refreshWeek(); 
    refreshAnalytics(); 
    refreshTargetsProgress();
  };
  $("btn-next-week").onclick = () => { 
    currentWeekStart = addDays(currentWeekStart, 7); 
    setStatus("Loading week...");
    refreshWeek(); 
    refreshAnalytics(); 
    refreshTargetsProgress();
  };
  $("btn-this-week").onclick = () => { 
    currentWeekStart = getSunday(new Date()); 
    refreshWeek(); 
    refreshAnalytics(); 
    refreshTargetsProgress();
  };
  
  const toggleGoogle = $("toggle-include-google");
  if (toggleGoogle) {
    toggleGoogle.checked = localStorage.getItem("timesense_include_google") === "1";
    toggleGoogle.onchange = (e) => {
      localStorage.setItem("timesense_include_google", e.target.checked ? "1" : "0");
      refreshWeek();
    };
  }
  const toggleOutlook = $("toggle-include-outlook");
  if (toggleOutlook) {
    toggleOutlook.checked = localStorage.getItem("timesense_include_outlook") === "1";
    toggleOutlook.onchange = (e) => {
      localStorage.setItem("timesense_include_outlook", e.target.checked ? "1" : "0");
      refreshWeek();
    };
  }
  const aiLangEl = $("ai-lang");
  if (aiLangEl) {
    aiLangEl.value = localStorage.getItem("timesense_ai_lang") || "en";
    aiLangEl.onchange = (e) => {
      localStorage.setItem("timesense_ai_lang", e.target.value);
      refreshAnalytics();
    };
  }
  const savedModel = getAiInsightsModel();
  const settingsModelSel = $("settings-ai-insights-model");
  const insightsModalSel = $("insights-modal-model");
  if (settingsModelSel) {
    settingsModelSel.value = savedModel;
    settingsModelSel.onchange = (e) => {
      setAiInsightsModel(e.target.value);
      refreshAnalytics();
    };
  }
  if (insightsModalSel) {
    insightsModalSel.value = savedModel;
    insightsModalSel.onchange = (e) => { setAiInsightsModel(e.target.value); };
  }
  const aiPlanningModelSel = $("ai-planning-model");
  if (aiPlanningModelSel) {
    aiPlanningModelSel.value = getAiPlanningModel();
    aiPlanningModelSel.onchange = (e) => localStorage.setItem("timesense_ai_planning_model", e.target.value);
  }
  $("toggle-show-night").checked = !hideNight;
  $("toggle-show-night").onchange = (e) => { hideNight = !e.target.checked; refreshWeek(); };

  // Calendar zoom
  const zoomEl = $("calendar-zoom");
  if (zoomEl) {
    zoomEl.value = String(getPixelsPerHour());
    zoomEl.oninput = () => { setCalendarZoom(parseInt(zoomEl.value, 10)); };
  }
  
  // Refresh: reload calendar (planned + logged), analytics, targets, goals, uncategorized, connections
  function doRefresh() {
    setStatus("Refreshing…");
    Promise.all([
      refreshWeek(),
      refreshAnalytics(),
      refreshTargetsProgress(),
      refreshGoals(),
      refreshUncategorized(),
      refreshConnections(),
    ]).then(() => { setStatus("Ready"); loadTrendsData(); }).catch(e => { setStatus(e.message || "Refresh failed"); console.error(e); });
  }
  const btnRefresh = $("btn-refresh");
  if (btnRefresh) {
    btnRefresh.title = "Refresh calendar (e.g. after Mac sync). Shortcut: Cmd+Option+R";
    btnRefresh.onclick = doRefresh;
  }
  // Quick sync: keyboard shortcut to refresh after syncing in Mac menu bar app
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.altKey && e.key === "r") {
      e.preventDefault();
      doRefresh();
    }
  });
  // Quick log delete toggle button
  const btnQuickDeleteToggle = $("btn-quick-log-delete-toggle");
  if (btnQuickDeleteToggle) {
    btnQuickDeleteToggle.onclick = (e) => {
      e.stopPropagation();
      quickLogDeleteMode = !quickLogDeleteMode;
      // Toggle visibility of all delete buttons
      document.querySelectorAll(".quick-btn-delete").forEach(btn => {
        btn.style.display = quickLogDeleteMode ? "" : "none";
      });
      btnQuickDeleteToggle.style.background = quickLogDeleteMode ? "var(--danger)" : "";
      btnQuickDeleteToggle.style.color = quickLogDeleteMode ? "white" : "";
    };
  }
  
  // Quick log add button (now in Quick Log panel header)
  const btnQuickAdd = $("btn-quick-log-add");
  if (btnQuickAdd) {
    btnQuickAdd.onclick = (e) => {
      e.stopPropagation();
      const catName = prompt("Category name to add as quick button (must match an existing category):", "");
      if (!catName || !catName.trim()) return;
      const cat = allCategories.find(c => (c.name || "").trim().toLowerCase() === catName.trim().toLowerCase());
      if (!cat) {
        setStatus(`No category named "${catName.trim()}". Add it in Settings → Categories first.`);
        return;
      }
      const wrap = $("quick-buttons");
      if (!wrap) return;
      const btnWrap = document.createElement("div");
      btnWrap.className = "quick-btn-wrapper";
      btnWrap.style.position = "relative";
      btnWrap.style.display = "inline-block";
      const btn = document.createElement("button");
      btn.className = "quick-btn";
      btn.textContent = cat.name;
      btn.dataset.categoryId = cat.id;
      btn.dataset.categoryName = cat.name;
      btn.onclick = () => quickLog(cat.id, cat.name);
      btnWrap.appendChild(btn);
      const delBtn = document.createElement("button");
      delBtn.className = "quick-btn-delete";
      delBtn.textContent = "×";
      delBtn.title = "Remove from quick log";
      delBtn.style.display = quickLogDeleteMode ? "" : "none";
      delBtn.onclick = (e) => {
        e.stopPropagation();
        btnWrap.remove();
        setStatus(`Removed "${cat.name}" from quick log`);
      };
      btnWrap.appendChild(delBtn);
      wrap.appendChild(btnWrap);
      setStatus(`Added "${cat.name}" to quick log`);
    };
  }
  
  // Settings modal
  $("btn-settings").onclick = openSettingsModal;
  $("btn-manage-targets").onclick = openSettingsModal;
  $("settings-close").onclick = () => $("settings-modal").style.display = "none";
  
  // Settings tabs
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.onclick = () => {
      switchTab(btn.dataset.tab);
      if (btn.dataset.tab === "categories") loadSettingsCategories();
    };
  });
  
  // Custom log (optional: Quick Log panel may be removed)
  if ($("btn-custom")) $("btn-custom").onclick = logCustomEntry;
  if ($("custom-title")) $("custom-title").onkeydown = (e) => { if (e.key === "Enter") logCustomEntry(); };
  
  // Sleep log (optional: Quick Log panel may be removed)
  if ($("btn-sleep-log")) $("btn-sleep-log").onclick = logSleep;
  
  // Goals
  $("btn-add-goal").onclick = () => $("goal-modal").style.display = "";
  $("goal-modal-close").onclick = () => $("goal-modal").style.display = "none";
  $("goal-cancel").onclick = () => $("goal-modal").style.display = "none";
  $("goal-save").onclick = saveGoal;
  
  // Edit modal
  $("modal-close").onclick = () => $("edit-modal").style.display = "none";
  $("edit-cancel").onclick = () => $("edit-modal").style.display = "none";
  $("edit-save").onclick = saveEditedEntry;
  
  // Context menu
  document.addEventListener("click", hideContextMenu);
  $("ctx-edit").onclick = () => { hideContextMenu(); openEditModal(); };
  $("ctx-delete").onclick = () => { hideContextMenu(); deleteSelectedEntry(); };
  $("ctx-convert").onclick = () => { hideContextMenu(); openEditModal(); };

  // Ctrl+Z / Cmd+Z undo
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "z") {
      e.preventDefault();
      undoLastAction();
    }
  });
  
  // Drag-add modal
  $("drag-add-modal-close").onclick = () => closeDragAddModal(true);
  $("drag-add-cancel").onclick = () => closeDragAddModal(true);
  $("drag-add-ok").onclick = confirmDragAdd;
  const dragAddModal = $("drag-add-modal");
  if (dragAddModal) {
    dragAddModal.onclick = (e) => { if (e.target === dragAddModal) closeDragAddModal(true); };
    const dragAddTitle = $("drag-add-title");
    if (dragAddTitle) dragAddTitle.onkeydown = (e) => { if (e.key === "Enter") confirmDragAdd(); };
  }

  // Close modals on overlay click (except drag-add which has its own handler)
  document.querySelectorAll(".modal-overlay").forEach(overlay => {
    if (overlay.id === "drag-add-modal") return;
    overlay.onclick = (e) => { if (e.target === overlay) overlay.style.display = "none"; };
  });
  
  // Insights modal
  $("btn-view-insights").onclick = openInsightsModal;
  $("analytics-header").onclick = openInsightsModal;
  $("insights-close").onclick = () => $("insights-modal").style.display = "none";
  $("btn-apply-range").onclick = () => loadInsightsData();
  
  // Quick range buttons in insights
  document.querySelectorAll(".range-btn").forEach(btn => {
    btn.onclick = () => {
      const days = parseInt(btn.dataset.range, 10) || 7;
      const end = formatDate(new Date());
      const start = addDays(end, -days + 1);
      if ($("insights-start")) $("insights-start").value = start;
      if ($("insights-end")) $("insights-end").value = end;
      loadInsightsData(start, end);
    };
  });

  // Trends panel (past N days chart)
  renderTrendsCategories();
  loadTrendsData();
  const trendsPeriod = $("trends-period");
  if (trendsPeriod) {
    trendsPeriod.onchange = () => {
      const customRange = $("trends-custom-range");
      if (customRange) customRange.style.display = trendsPeriod.value === "custom" ? "flex" : "none";
      if (trendsPeriod.value === "custom") {
        const end = formatDate(new Date());
        if ($("trends-start")) $("trends-start").value = addDays(end, -6);
        if ($("trends-end")) $("trends-end").value = end;
      }
      loadTrendsData();
    };
  }
  if ($("trends-start")) $("trends-start").onchange = () => loadTrendsData();
  if ($("trends-end")) $("trends-end").onchange = () => loadTrendsData();
  
  // Targets
  $("btn-add-target").onclick = addTarget;
  setupCategoriesForm();
  const categorySelect = $("new-target-category");
  const customCategoryInput = $("new-target-category-custom");
  if (categorySelect && customCategoryInput) {
    categorySelect.onchange = () => {
      customCategoryInput.style.display = categorySelect.value === "__custom__" ? "inline-block" : "none";
      if (categorySelect.value !== "__custom__") customCategoryInput.value = "";
    };
    if (categorySelect.value === "__custom__") customCategoryInput.style.display = "inline-block";
  }
  document.querySelectorAll(".preset-btn").forEach(btn => {
    btn.onclick = () => applyPreset(btn.dataset.preset);
  });
  
  // AI Planning
  $("btn-get-ai-advice").onclick = getAIAdvice;
  
  // Push & Install
  $("btn-push").onclick = enablePush;
  $("btn-push-test").onclick = testPush;
  
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    window.deferredInstallPrompt = e;
    $("btn-install").disabled = false;
  });
  $("btn-install").onclick = () => {
    if (window.deferredInstallPrompt) {
      window.deferredInstallPrompt.prompt();
      window.deferredInstallPrompt = null;
    }
  };
  
  $("btn-google").onclick = () => { window.location.href = "/auth/google/start"; };
  if ($("settings-btn-google")) $("settings-btn-google").onclick = () => { window.location.href = "/auth/google/start"; };
  
  // Auto-categorize button
  if ($("btn-auto-categorize")) {
    $("btn-auto-categorize").onclick = autoCategorizAll;
  }

  // Day note: default to today, load on init and when date changes
  const dayNoteInput = $("day-note-input");
  const dayNoteText = $("day-note-text");
  const dayNoteSave = $("day-note-save");
  if (dayNoteInput && dayNoteText && dayNoteSave) {
    dayNoteInput.value = formatDate(new Date());
    dayNoteInput.onchange = () => loadDayNote(dayNoteInput.value);
    dayNoteSave.onclick = () => saveDayNote(dayNoteInput.value);
    loadDayNote(dayNoteInput.value);
  }

  const dayNoteAnalyze = $("day-note-analyze");
  if (dayNoteAnalyze) {
    dayNoteAnalyze.onclick = () => runDayAnalysis(dayNoteInput?.value || formatDate(new Date()));
  }
  const dayReflectionModelSel = $("day-reflection-model");
  if (dayReflectionModelSel) {
    dayReflectionModelSel.value = getDayReflectionModel();
    dayReflectionModelSel.onchange = () => setDayReflectionModel(dayReflectionModelSel.value);
  }
}

// ─────────────────────────────────────────────────────────────
// Quick Log
// ─────────────────────────────────────────────────────────────
let quickLogDeleteMode = false; // Track if delete mode is active

async function renderQuickButtons() {
  try {
    // Use all categories so "Unplanned wasting" etc. have correct ids (prompt list only had first 6)
    let cats = allCategories && allCategories.length > 0 ? allCategories : await apiGet("/api/categories");
    if (!cats || cats.length === 0) cats = await apiGet("/api/categories/prompt");
    const wrap = $("quick-buttons");
    if (!wrap) return;
    wrap.innerHTML = "";
    const showCount = Math.min(cats.length, 15);
    for (const c of cats.slice(0, showCount)) {
      const btnWrap = document.createElement("div");
      btnWrap.className = "quick-btn-wrapper";
      btnWrap.style.position = "relative";
      btnWrap.style.display = "inline-block";
      const catId = c.id;
      const catName = c.name;
      const btn = document.createElement("button");
      btn.className = "quick-btn";
      btn.textContent = catName;
      btn.dataset.categoryId = catId;
      btn.dataset.categoryName = catName;
      btn.onclick = () => quickLog(catId, catName);
      btnWrap.appendChild(btn);
      const delBtn = document.createElement("button");
      delBtn.className = "quick-btn-delete";
      delBtn.textContent = "×";
      delBtn.title = "Remove from quick log";
      delBtn.style.display = quickLogDeleteMode ? "" : "none";
      delBtn.onclick = (e) => {
        e.stopPropagation();
        btnWrap.remove();
        setStatus(`Removed "${catName}" from quick log`);
      };
      btnWrap.appendChild(delBtn);
      wrap.appendChild(btnWrap);
    }
  } catch (e) {
    console.error("Failed to render quick buttons:", e);
  }
}

async function quickLog(categoryId, categoryName) {
  let startAt, endAt;
  const titleInput = $("custom-title");
  const title = (titleInput && titleInput.value) ? titleInput.value.trim() : "";

  if (pendingQuickLogChunk) {
    startAt = new Date(pendingQuickLogChunk.start_at);
    endAt = new Date(pendingQuickLogChunk.end_at);
    pendingQuickLogChunk = null;
    updatePendingChunkHint();
    // Remove the dashed selection box
    const pendingSel = document.querySelector(".drag-selection-pending");
    if (pendingSel) pendingSel.remove();
  } else {
    const durationSelect = $("quick-duration");
    const durationValue = durationSelect ? durationSelect.value : "30";
    const now = new Date();
    if (durationValue === "custom") {
      const startStr = prompt("Start time (e.g., 14:30):", "");
      const endStr = prompt("End time (e.g., 15:30):", "");
      if (!startStr || !endStr) {
        setStatus("Cancelled");
        return;
      }
      startAt = parseTimeToDate(startStr, now);
      endAt = parseTimeToDate(endStr, now);
      if (!startAt || !endAt || endAt <= startAt) {
        setStatus("Invalid time range");
        return;
      }
    } else {
      const mins = parseInt(durationValue) || 30;
      endAt = now;
      startAt = new Date(now.getTime() - mins * 60 * 1000);
    }
  }

  // Optimistic update: create temporary event immediately
  const dayStr = startAt.toISOString().split('T')[0]; // YYYY-MM-DD format
  const tempEntry = {
    id: `temp-${Date.now()}`,
    title: title || "",
    category_id: categoryId,
    category_name: categoryName,
    start_at: startAt.toISOString(),
    end_at: endAt.toISOString(),
    day: dayStr,
    _isTemp: true
  };
  
  // Add to current logged events and re-render immediately
  const currentDays = Array.from({ length: 7 }, (_, i) => addDays(currentWeekStart, i));
  const currentLogged = Array.from(document.querySelectorAll('.week-event.logged')).map(el => {
    try {
      return JSON.parse(el.dataset.entry);
    } catch {
      return null;
    }
  }).filter(Boolean);
  currentLogged.push(tempEntry);
  
  // Get current planned events
  const currentPlanned = Array.from(document.querySelectorAll('.week-event.planned')).map(el => {
    try {
      return JSON.parse(el.dataset.entry);
    } catch {
      return null;
    }
  }).filter(Boolean);
  
  renderWeekCalendar(currentDays, currentPlanned, currentLogged);
  
  setStatus(`✓ Logged ${categoryName}`);
  if (titleInput) titleInput.value = "";

  // Fire API in background so user already sees the event; refresh when done to replace temp with real
  apiPost("/api/quick_log", {
    category_id: categoryId,
    title: title || "",
    tags: [],
    device: "web",
    source: "manual",
    start_at: startAt.toISOString(),
    end_at: endAt.toISOString(),
  }).then(() => {
    Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
  }).catch(e => {
    setStatus(`Error: ${e.message}`);
    refreshWeek(); // Remove temp entry on error
  });
}

function updatePendingChunkHint() {
  const el = $("pending-chunk-hint");
  if (!el) return;
  if (!pendingQuickLogChunk) {
    el.textContent = "";
    el.style.display = "none";
    return;
  }
  const s = new Date(pendingQuickLogChunk.start_at);
  const e = new Date(pendingQuickLogChunk.end_at);
  el.textContent = `Time slot: ${fmtTime(s)} – ${fmtTime(e)} — pick a category below or type title and click +`;
  el.style.display = "";
}

function openDragAddModal(startTime, endTime, selectionEl) {
  const modal = $("drag-add-modal");
  const titleInput = $("drag-add-title");
  const categorySelect = $("drag-add-category");
  if (!modal || !titleInput || !categorySelect) return;
  titleInput.value = "";
  categorySelect.innerHTML = '<option value="">Auto from title</option>';
  for (const c of allCategories) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    categorySelect.appendChild(opt);
  }
  modal._dragStartTime = startTime;
  modal._dragEndTime = endTime;
  modal._dragSelectionEl = selectionEl;
  modal.style.display = "";
  titleInput.focus();
}

function closeDragAddModal(clearPending) {
  const modal = $("drag-add-modal");
  if (!modal) return;
  modal.style.display = "none";
  if (clearPending) {
    pendingQuickLogChunk = null;
    updatePendingChunkHint();
    const sel = modal._dragSelectionEl;
    if (sel) sel.remove();
    modal._dragSelectionEl = null;
  }
}

function confirmDragAdd() {
  const modal = $("drag-add-modal");
  const titleInput = $("drag-add-title");
  const categorySelect = $("drag-add-category");
  if (!modal || !pendingQuickLogChunk) return;
  const startTime = modal._dragStartTime;
  const endTime = modal._dragEndTime;
  const selectionEl = modal._dragSelectionEl;
  const title = (titleInput && titleInput.value) ? titleInput.value.trim() : "";
  const categoryId = (categorySelect && categorySelect.value) ? categorySelect.value.trim() : "";

  const dayStr = startTime.toISOString().split("T")[0];
  const cat = categoryId ? allCategories.find(c => c.id === categoryId) : null;

  if (cat) {
    closeDragAddModal(true);
    if (selectionEl) selectionEl.remove();
    // User picked a category: use it directly (no auto_categorize)
    const tempEntry = {
      id: `temp-${Date.now()}`,
      title: title || "",
      category_id: cat.id,
      category_name: cat.name,
      start_at: startTime.toISOString(),
      end_at: endTime.toISOString(),
      day: dayStr,
      _isTemp: true,
    };
    const currentDays = Array.from({ length: 7 }, (_, i) => addDays(currentWeekStart, i));
    const currentLogged = Array.from(document.querySelectorAll(".week-event.logged")).map(el => {
      try { return JSON.parse(el.dataset.entry); } catch { return null; }
    }).filter(Boolean);
    currentLogged.push(tempEntry);
    const currentPlanned = Array.from(document.querySelectorAll(".week-event.planned")).map(el => {
      try { return JSON.parse(el.dataset.entry); } catch { return null; }
    }).filter(Boolean);
    renderWeekCalendar(currentDays, currentPlanned, currentLogged);
    setStatus("✓ Logged " + cat.name);
    apiPost("/api/quick_log", {
      category_id: cat.id,
      title: title || "",
      tags: [],
      device: "web",
      source: "manual",
      start_at: startTime.toISOString(),
      end_at: endTime.toISOString(),
    }).then((created) => {
      undoStack = created?.id ? { type: "create", createdId: created.id } : null;
      setStatus("✓ Entry created (Ctrl+Z to undo)");
      Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
    }).catch(e => {
      setStatus("Error: " + e.message);
      refreshWeek();
    });
    return;
  }

  if (title) {
    closeDragAddModal(true);
    if (selectionEl) selectionEl.remove();
    // Auto from title: use auto_categorize then quick_log
    const tempEntry = {
      id: `temp-${Date.now()}`,
      title,
      category_id: "",
      category_name: "Other",
      start_at: startTime.toISOString(),
      end_at: endTime.toISOString(),
      day: dayStr,
      _isTemp: true,
    };
    const currentDays = Array.from({ length: 7 }, (_, i) => addDays(currentWeekStart, i));
    const currentLogged = Array.from(document.querySelectorAll(".week-event.logged")).map(el => {
      try { return JSON.parse(el.dataset.entry); } catch { return null; }
    }).filter(Boolean);
    currentLogged.push(tempEntry);
    const currentPlanned = Array.from(document.querySelectorAll(".week-event.planned")).map(el => {
      try { return JSON.parse(el.dataset.entry); } catch { return null; }
    }).filter(Boolean);
    renderWeekCalendar(currentDays, currentPlanned, currentLogged);
    setStatus("✓ Entry added (categorizing…)");
    const useAi = !!($("toggle-use-ai-categorize") && $("toggle-use-ai-categorize").checked);
    apiPost("/api/auto_categorize", { title, use_ai: useAi })
      .then((catResult) => {
        const cid = catResult.category_id;
        if (!cid) return refreshWeek();
        return apiPost("/api/quick_log", {
          category_id: cid,
          title,
          tags: [],
          device: "web",
          source: "manual",
          start_at: startTime.toISOString(),
          end_at: endTime.toISOString(),
        }).then((created) => {
          undoStack = created?.id ? { type: "create", createdId: created.id } : null;
          setStatus("✓ Entry created (Ctrl+Z to undo)");
          Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
        });
      })
      .catch((err) => {
        setStatus("Error: " + err.message);
        refreshWeek();
      });
    return;
  }

  // No category and no title: close modal but keep pending and selection so they can pick below
  closeDragAddModal(false);
  setStatus("Pick a category below or type title and click +");
  const quickPanel = document.querySelector(".panel");
  if (quickPanel) quickPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function logCustomEntry() {
  const title = $("custom-title").value.trim();
  let startAt, endAt;

  if (pendingQuickLogChunk) {
    startAt = new Date(pendingQuickLogChunk.start_at);
    endAt = new Date(pendingQuickLogChunk.end_at);
    pendingQuickLogChunk = null;
    updatePendingChunkHint();
    // Remove the dashed selection box
    const pendingSel = document.querySelector(".drag-selection-pending");
    if (pendingSel) pendingSel.remove();
  }

  if (!startAt || !endAt) {
    if (!title) {
      setStatus("Enter what you did");
      return;
    }
    const durationSelect = $("quick-duration");
    const durationValue = durationSelect.value;
    const now = new Date();
    if (durationValue === "custom") {
      const startStr = prompt("Start time (e.g., 14:30 or 2:30 PM):", "");
      const endStr = prompt("End time (e.g., 15:30 or 3:30 PM):", "");
      if (!startStr || !endStr) {
        setStatus("Cancelled");
        return;
      }
      startAt = parseTimeToDate(startStr, now);
      endAt = parseTimeToDate(endStr, now);
      if (!startAt || !endAt || endAt <= startAt) {
        setStatus("Invalid time range");
        return;
      }
    } else {
      const mins = parseInt(durationValue);
      endAt = now;
      startAt = new Date(now.getTime() - mins * 60 * 1000);
    }
  }

  try {
    
    // Auto-categorize based on title (use AI if setting enabled)
    setStatus("Categorizing...");
    const useAi = !!($("toggle-use-ai-categorize") && $("toggle-use-ai-categorize").checked);
    const catResult = await apiPost("/api/auto_categorize", { title, use_ai: useAi });
    const categoryId = catResult.category_id;
    
    setStatus("Logging...");
      const created = await apiPost("/api/quick_log", {
        category_id: categoryId,
        title,
      tags: [],
      device: "web",
        source: "manual",
      start_at: startAt.toISOString(),
      end_at: endAt.toISOString(),
      });
      undoStack = created?.id ? { type: "create", createdId: created.id } : null;
      $("custom-title").value = "";
    setStatus(`✓ Logged: ${title} (Ctrl+Z to undo)`);
    Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
    } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

async function logSleep() {
  const startVal = $("sleep-start").value;
  const endVal = $("sleep-end").value;
  if (!startVal || !endVal) {
    setStatus("Enter sleep times");
    return;
  }
  
  const today = formatDate(new Date());
  let start = new Date(`${today}T${startVal}:00`);
  let end = new Date(`${today}T${endVal}:00`);
  
  if (end <= start) {
    start = new Date(`${addDays(today, -1)}T${startVal}:00`);
  }
  
  const sleepCat = allCategories.find(c => c.name.toLowerCase().includes("sleep"));
  if (!sleepCat) {
    setStatus("Sleep category not found");
      return;
    }
  
  setStatus("Logging sleep...");
    try {
      await apiPost("/api/quick_log", {
      category_id: sleepCat.id,
      title: "Sleep",
      tags: [],
      device: "web",
        source: "manual",
      start_at: start.toISOString(),
      end_at: end.toISOString(),
    });
    setStatus(`✓ Logged sleep ${fmtTime(start)} → ${fmtTime(end)}`);
    Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
    } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

// ─────────────────────────────────────────────────────────────
// Week Calendar
// ─────────────────────────────────────────────────────────────
async function refreshWeek() {
  const days = Array.from({ length: 7 }, (_, i) => addDays(currentWeekStart, i));
  const endDay = addDays(currentWeekStart, 6);
  
  const startDate = new Date(`${currentWeekStart}T00:00:00`);
  const endDate = new Date(`${endDay}T00:00:00`);
  const startMonth = startDate.toLocaleDateString([], { month: "short", day: "numeric" });
  const endMonth = endDate.toLocaleDateString([], { month: "short", day: "numeric" });
  const weekLabel = $("week-label");
  if (weekLabel) weekLabel.textContent = `${startMonth} – ${endMonth}`;

  // Paint calendar grid immediately so main area is never white while data loads
  renderWeekCalendar(days, [], []);

  const loadingEl = $("week-loading");
  if (loadingEl) loadingEl.style.display = "";
  setStatus("Loading week…");

  const includeGoogle = localStorage.getItem("timesense_include_google") === "1";
  const includeOutlook = localStorage.getItem("timesense_include_outlook") === "1";
  const WEEK_FETCH_TIMEOUT_MS = 10000; // Reduced timeout since backend is fast now
  const timeoutPromise = new Promise((_, reject) =>
    setTimeout(() => reject(new Error("Week data request timed out")), WEEK_FETCH_TIMEOUT_MS)
  );
  let planned = [], logged = [];
  try {
    const startTime = Date.now();
    [planned, logged] = await Promise.race([
      Promise.all([
        apiGet(`/api/planned_events_range?start_day=${currentWeekStart}&days=7&include_google=${includeGoogle ? "true" : "false"}&include_outlook=${includeOutlook ? "true" : "false"}`, { cache: "no-store" }),
        apiGet(`/api/time_entries_range?start_day=${currentWeekStart}&days=7`),
      ]),
      timeoutPromise,
    ]);
    const elapsed = Date.now() - startTime;
    if (elapsed > 1000) console.log(`Week loaded in ${elapsed}ms`);
  } catch (e) {
    console.error("Failed to load week data:", e);
    setStatus(e.message || "Week load failed");
  } finally {
    if (loadingEl) loadingEl.style.display = "none";
  }
  renderWeekCalendar(days, planned, logged);
  setStatus("Ready");
}

function renderWeekCalendar(days, planned, logged) {
  const hoursEl = $("week-hours");
  const canvas = $("week-canvas");
  const allDayBar = $("week-allday-bar");
  
  if (!hoursEl || !canvas) return;
  
  const [startHour, endHour] = getVisibleHourRange();
  const visibleHeight = (endHour - startHour) * getPixelsPerHour();
  const headerHeight = 52;
  
  // Group events by day
  const plannedByDay = new Map();
  const loggedByDay = new Map();
  const allDayByDay = new Map();
  
  for (const day of days) {
    plannedByDay.set(day, []);
    loggedByDay.set(day, []);
    allDayByDay.set(day, []);
  }
  
  const seenPlanned = new Set();
  const allDayEventIds = new Set(); // Track which events are all-day so we don't show them in multiple day columns
  for (const ev of (planned || [])) {
    const day = ev.day || days[0];
    const key = `${day}:${ev.id}:${ev.start_at}`;
    if (seenPlanned.has(key)) continue;
    seenPlanned.add(key);
    const s = new Date(ev.start_at);
    const e = new Date(ev.end_at);
    // Check if this segment spans most of the day (starts near 00:00 or ends near 23:59) OR duration >= 23h
    const dayStart = new Date(`${day}T00:00:00`);
    const startMins = minsBetween(dayStart, s);
    const endMins = minsBetween(dayStart, e);
    const durationMins = minsBetween(s, e);
    const isAllDay = durationMins >= 23 * 60 || (startMins <= 60 && endMins >= 22 * 60); // Starts within 1h of midnight and ends within 1h of day end, or >= 23h duration
    if (isAllDay) {
      // Only show all-day event in the FIRST day it appears (not spanning multiple days)
      if (!allDayEventIds.has(ev.id) && allDayByDay.has(day)) {
        allDayEventIds.add(ev.id);
        allDayByDay.get(day).push(ev);
      }
    } else {
      if (plannedByDay.has(day)) plannedByDay.get(day).push(ev);
    }
  }
  
  const seenLogged = new Set();
  for (const ev of (logged || [])) {
    const day = ev.day || days[0];
    const key = `${day}:${ev.id}:${ev.start_at}`;
    if (seenLogged.has(key)) continue;
    seenLogged.add(key);
    const s = new Date(ev.start_at);
    const e = new Date(ev.end_at);
    // Whole-day logged events go in the top bar only (Google Calendar style), not as 0–24 chunk in grid
    // Check if this segment spans most of the day (starts near 00:00 or ends near 23:59) OR duration >= 23h
    const dayStart = new Date(`${day}T00:00:00`);
    const dayEnd = new Date(`${day}T23:59:59`);
    const startMins = minsBetween(dayStart, s);
    const endMins = minsBetween(dayStart, e);
    const durationMins = minsBetween(s, e);
    const isAllDay = durationMins >= 23 * 60 || (startMins <= 60 && endMins >= 22 * 60); // Starts within 1h of midnight and ends within 1h of day end, or >= 23h duration
    if (isAllDay) {
      // Only show all-day event in the FIRST day it appears (not spanning multiple days)
      if (!allDayEventIds.has(ev.id) && allDayByDay.has(day)) {
        allDayEventIds.add(ev.id);
        allDayByDay.get(day).push(ev);
      }
    } else {
      if (loggedByDay.has(day)) loggedByDay.get(day).push(ev);
    }
  }

  // All-day bar (little box on top of each day, like Google Calendar)
  let hasAllDay = false;
  for (const evs of allDayByDay.values()) {
    if (evs.length > 0) { hasAllDay = true; break; }
  }

  if (allDayBar) {
    if (hasAllDay) {
      allDayBar.style.display = "";
      allDayBar.innerHTML = '<div class="week-allday-col"></div>';
      for (const day of days) {
        const col = document.createElement("div");
        col.className = "week-allday-col";
        const dayEvents = allDayByDay.get(day) || [];
        for (const ev of dayEvents.slice(0, 5)) {
          const chip = document.createElement("div");
          chip.className = "week-allday-chip";
          const text = ev.summary != null ? ev.summary : (ev.category_name && ev.title ? `${ev.category_name}: ${ev.title}` : (ev.category_name || ev.title || "All-day"));
          chip.textContent = text.length > 20 ? text.substring(0, 17) + "..." : text;
          chip.title = text; // Full text on hover
          chip.style.cursor = "pointer";
          // Apply category color (logged category_name or planned suggested_category)
          const chipCat = ev.category_name || ev.suggested_category;
          if (chipCat) {
            const bgColor = getCategoryColorForPanel(chipCat);
            chip.style.background = bgColor;
            chip.style.borderColor = bgColor;
            chip.style.color = "#1e293b";
            chip.style.opacity = "1";
          } else if (ev.summary) {
            chip.style.background = "var(--planned-bg)";
            chip.style.borderColor = "var(--planned-border)";
          }
          // Make all-day events editable: right-click to edit (left-click shows details)
          chip.oncontextmenu = (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (ev.category_id && ev.category_name) {
              // It's a logged entry - open edit modal
              selectedEntry = { id: ev.id, title: ev.title || "", category_id: ev.category_id, start_at: ev.start_at, end_at: ev.end_at, category_name: ev.category_name };
              selectedPlannedEvent = null;
              $("ctx-delete").style.display = "";
              $("ctx-convert").style.display = "none";
              showContextMenu(e.clientX, e.clientY);
            } else if (ev.id) {
              // It's a planned event - convert to log
              selectedPlannedEvent = { id: ev.id, title: ev.summary || "", start_at: ev.start_at, end_at: ev.end_at };
              selectedEntry = null;
              $("ctx-delete").style.display = "none";
              $("ctx-convert").style.display = "";
              showContextMenu(e.clientX, e.clientY);
            }
          };
          col.appendChild(chip);
        }
        allDayBar.appendChild(col);
      }
    } else {
      allDayBar.style.display = "none";
    }
  }
  
  // Hour labels
  hoursEl.innerHTML = "";
  for (let h = startHour; h < endHour; h++) {
    const label = document.createElement("div");
    label.className = "hour-label";
    label.style.top = `${headerHeight + (h - startHour) * getPixelsPerHour()}px`;
    label.textContent = `${String(h).padStart(2, "0")}:00`;
    hoursEl.appendChild(label);
  }
  
  // Canvas
  canvas.innerHTML = "";
  canvas.style.height = `${headerHeight + visibleHeight}px`;
  
  const todayStr = formatDate(new Date());
  const daySummaries = [];
  
  for (let i = 0; i < 7; i++) {
    const day = days[i];
    const col = document.createElement("div");
    col.className = "week-col" + (day === todayStr ? " today" : "");
    col.dataset.day = day;
    
    // Header
    const header = document.createElement("div");
    header.className = "week-day-header";
    const dateObj = new Date(`${day}T00:00:00`);
    header.innerHTML = `
      <div class="day-num">${dateObj.getDate()}</div>
      <div class="day-name">${["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][dateObj.getDay()]}</div>
    `;
    col.appendChild(header);
    
    const px = getPixelsPerHour();
    // Hour lines (with major lines every 2 hours and half-hour lines)
    for (let h = startHour; h < endHour; h++) {
      const fullHour = headerHeight + (h - startHour) * px;
      const line = document.createElement("div");
      line.className = "week-hour-line" + (h % 2 === 0 ? " major" : "");
      line.style.top = `${fullHour}px`;
      col.appendChild(line);
      const halfLine = document.createElement("div");
      halfLine.className = "week-half-line";
      halfLine.style.top = `${fullHour + px / 2}px`;
      col.appendChild(halfLine);
    }
    
    // Single combined layout for planned + logged so they never overlap (same time slot = same column pool)
    const plannedList = plannedByDay.get(day) || [];
    const loggedList = (loggedByDay.get(day) || []).slice().sort((a, b) => {
      const aSleep = (a.category_name || "").toLowerCase().includes("sleep");
      const bSleep = (b.category_name || "").toLowerCase().includes("sleep");
      if (aSleep && !bSleep) return -1;
      if (!aSleep && bSleep) return 1;
      return new Date(a.start_at) - new Date(b.start_at);
    });
    const combinedList = [
      ...plannedList.map(ev => ({ ...ev, _type: "planned" })),
      ...loggedList.map(ev => ({ ...ev, _type: "logged" })),
    ].sort((a, b) => new Date(a.start_at) - new Date(b.start_at));
    const combinedLayout = buildOverlapLayout(combinedList, ev => new Date(ev.start_at), ev => new Date(ev.end_at), 3);
    for (const ev of combinedLayout) {
      const type = ev.item._type || "planned";
      renderEvent(col, ev, day, startHour, headerHeight, type);
    }
    
    // Per-day stats: collect for summary row below grid (top 5 + total)
    const dayMinsByCat = {};
    let dayTotalMins = 0;
    for (const ev of loggedList) {
      const start = new Date(ev.start_at);
      const end = new Date(ev.end_at);
      const mins = Math.round((end - start) / 60000);
      if (mins <= 0) continue;
      const cat = ev.category_name || "Other";
      dayMinsByCat[cat] = (dayMinsByCat[cat] || 0) + mins;
      dayTotalMins += mins;
    }
    const top5 = Object.entries(dayMinsByCat)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([cat, m]) => ({ cat, hours: (m / 60).toFixed(1) }));
    daySummaries.push({
      day,
      top5,
      totalHours: (dayTotalMins / 60).toFixed(1),
      title: Object.entries(dayMinsByCat).map(([c, m]) => `${c}: ${(m / 60).toFixed(1)}h`).join("\n") || "No time logged",
      isToday: day === todayStr,
    });
    
    // Drag-to-create
    setupDragToCreate(col, day, startHour, headerHeight);
    
    canvas.appendChild(col);
  }
  
  // Summary row: below the 24h grid, same horizontal alignment (7 cells)
  const spacer = $("week-canvas-spacer");
  const summaryRow = $("week-summary-row");
  if (spacer) spacer.style.height = `${headerHeight + visibleHeight}px`;
  if (summaryRow) {
    summaryRow.innerHTML = "";
    summaryRow.style.display = "grid";
    daySummaries.forEach(({ day, top5, totalHours, title, isToday }) => {
      const cell = document.createElement("div");
      cell.className = "week-summary-cell" + (isToday ? " today" : "");
      const section = document.createElement("div");
      section.className = "week-day-summary-section";
      section.title = title;
      const titleEl = document.createElement("div");
      titleEl.className = "week-day-summary-title";
      titleEl.textContent = "Summary";
      section.appendChild(titleEl);
      const listEl = document.createElement("ul");
      listEl.className = "week-day-summary-list";
      if (top5.length) {
        top5.forEach(({ cat, hours }) => {
          const li = document.createElement("li");
          li.textContent = `${cat}: ${hours}h`;
          listEl.appendChild(li);
        });
      } else {
        const li = document.createElement("li");
        li.textContent = "—";
        li.classList.add("week-day-summary-empty");
        listEl.appendChild(li);
      }
      section.appendChild(listEl);
      const totalEl = document.createElement("div");
      totalEl.className = "week-day-summary-total";
      totalEl.textContent = `Total: ${totalHours}h`;
      section.appendChild(totalEl);
      cell.appendChild(section);
      summaryRow.appendChild(cell);
    });
  }
  
  // Scroll to current time
  const grid = $("week-grid");
  if (grid) {
    const px = getPixelsPerHour();
    const nowMins = new Date().getHours() * 60 + new Date().getMinutes();
    grid.scrollTop = Math.max(0, (nowMins - startHour * 60) * (px / 60) - 120);
  }
}

function renderEvent(col, ev, day, startHour, headerHeight, type) {
  const dayStart = new Date(`${day}T00:00:00`);
  const s = new Date(ev.item.start_at);
  const e = new Date(ev.item.end_at);
  
  const startMin = minsBetween(dayStart, s);
  const endMin = minsBetween(dayStart, e);
  const visibleStart = startHour * 60;
  const visibleEnd = 24 * 60;
  const clippedStart = Math.max(startMin, visibleStart);
  const clippedEnd = Math.min(endMin, visibleEnd);
  if (clippedEnd <= clippedStart) return;
  const px = getPixelsPerHour();
  const top = headerHeight + (clippedStart - visibleStart) * (px / 60);
  const height = Math.max(18, (clippedEnd - clippedStart) * (px / 60));
  
  const el = document.createElement("div");
  el.className = `week-event ${type}`;
  el.style.top = `${top}px`;
  el.style.height = `${height}px`;
  const categoryForColor = type === "logged" ? ev.item.category_name : ev.item.suggested_category;
  if (categoryForColor) {
    const bg = getCategoryColorForPanel(categoryForColor);
    if (bg) {
      el.style.background = bg;
      el.style.borderColor = "rgba(0,0,0,0.12)";
      el.style.color = "#1e293b";
      el.style.opacity = "1";
    }
  }
  const colWidth = 100 / ev.cols;
  el.style.left = `calc(${colWidth * ev.col}% + 2px)`;
  el.style.width = `calc(${colWidth}% - 4px)`;
  el.style.minWidth = "52px";
  
  const isMore = ev.item._isMore === true;
  const title = isMore
    ? (ev.item.title || "+? more")
    : (type === "logged"
      ? (ev.item.title ? `${ev.item.category_name}: ${ev.item.title}` : ev.item.category_name || "Logged")
      : (ev.item.summary || "Planned"));
  
  el.title = isMore && ev.item.summary ? ev.item.summary : title;
  el.classList.toggle("week-event-more", isMore);
  el.innerHTML = `
    <div class="event-title">${escapeHtml(title)}</div>
    ${height >= 32 && !isMore ? `<div class="event-meta">${fmtTime(s)} – ${fmtTime(e)}</div>` : ""}
  `;
  
  el.dataset.entry = JSON.stringify(ev.item);
  el.dataset.type = type;
  
  if (isMore) {
    el.oncontextmenu = (e) => e.preventDefault();
    col.appendChild(el);
    return;
  }
  
  el.oncontextmenu = (e) => {
    e.preventDefault();
    if (type === "logged") {
      selectedEntry = ev.item;
      selectedPlannedEvent = null;
      // Show delete, hide convert
      $("ctx-delete").style.display = "";
      $("ctx-convert").style.display = "none";
    } else {
      selectedEntry = null;
      selectedPlannedEvent = ev.item;
      // Hide delete for planned events (can't delete external events), show convert
      $("ctx-delete").style.display = "none";
      $("ctx-convert").style.display = "";
    }
    showContextMenu(e.clientX, e.clientY);
  };
  
  col.appendChild(el);
}

const MAX_OVERLAP_COLUMNS_PLANNED = 2;
const MAX_OVERLAP_COLUMNS_LOGGED = 3;

function buildOverlapLayout(items, getStart, getEnd, maxColumns = MAX_OVERLAP_COLUMNS_LOGGED) {
  if (!items || items.length === 0) return [];
  
  const events = items.map(it => ({
    it,
    start: getStart(it),
    end: getEnd(it),
  })).filter(e => e.start && e.end && e.end > e.start)
    .sort((a, b) => a.start - b.start);
  
  if (events.length === 0) return [];
  
  const groups = [];
  let current = [];
  let currentEnd = null;
  
  for (const ev of events) {
    if (!current.length || ev.start < currentEnd) {
      current.push(ev);
      currentEnd = currentEnd ? new Date(Math.max(currentEnd.getTime(), ev.end.getTime())) : ev.end;
    } else {
      groups.push(current);
      current = [ev];
      currentEnd = ev.end;
    }
  }
  if (current.length) groups.push(current);
  
  const layout = [];
  for (const group of groups) {
    const cols = [];
    const assigned = [];
    for (const ev of group) {
      let placed = false;
      for (let i = 0; i < cols.length; i++) {
        if (ev.start >= cols[i]) {
          cols[i] = ev.end;
          assigned.push({ ev, col: i });
          placed = true;
          break;
        }
      }
      if (!placed) {
        cols.push(ev.end);
        assigned.push({ ev, col: cols.length - 1 });
      }
    }
    const n = assigned.length;
    const colsToShow = Math.min(cols.length, maxColumns);
    const hiddenCount = n > colsToShow ? n - colsToShow : 0;
    const displayCols = hiddenCount > 0 ? colsToShow + 1 : colsToShow;
    for (let i = 0; i < assigned.length; i++) {
      const { ev, col } = assigned[i];
      if (i < colsToShow) {
        layout.push({ item: ev.it, col: col, cols: displayCols });
      } else if (i === colsToShow && hiddenCount > 0) {
        const firstHidden = assigned[colsToShow].ev;
        const moreTitles = assigned.slice(colsToShow).map(a => {
          const t = a.ev.it.title || a.ev.it.summary || a.ev.it.category_name || "Event";
          return (typeof t === "string" && t.length > 30) ? t.slice(0, 27) + "…" : t;
        }).join("\n");
        const startIso = firstHidden.start && firstHidden.start.toISOString ? firstHidden.start.toISOString() : new Date().toISOString();
        const endIso = firstHidden.end && firstHidden.end.toISOString ? firstHidden.end.toISOString() : new Date().toISOString();
        layout.push({
          item: {
            id: "_more",
            title: `+${hiddenCount} more`,
            summary: moreTitles,
            start_at: startIso,
            end_at: endIso,
            _isMore: true,
          },
          col: colsToShow,
          cols: displayCols,
        });
        break;
      }
    }
  }
  return layout;
}

// ─────────────────────────────────────────────────────────────
// Drag to Create
// ─────────────────────────────────────────────────────────────
function setupDragToCreate(col, day, startHour, headerHeight) {
  let dragging = false;
  let startY = 0;
  let selectionEl = null;
  
  col.addEventListener("mousedown", (e) => {
    if (e.target.closest(".week-event")) return;
    if (e.button !== 0) return;
    
    dragging = true;
    startY = e.offsetY;
    
    selectionEl = document.createElement("div");
    selectionEl.className = "drag-selection";
    selectionEl.style.left = "2px";
    selectionEl.style.right = "2px";
    col.appendChild(selectionEl);
    
    e.preventDefault();
  });
  
  col.addEventListener("mousemove", (e) => {
    if (!dragging || !selectionEl) return;
    
    const currentY = e.offsetY;
    const top = Math.min(startY, currentY);
    const height = Math.abs(currentY - startY);
    
    selectionEl.style.top = `${top}px`;
    selectionEl.style.height = `${Math.max(15, height)}px`;
  });
  
  const endDrag = async (e) => {
    if (!dragging || !selectionEl) return;
    dragging = false;
    
    const currentY = e.offsetY || e.clientY - col.getBoundingClientRect().top;
    const top = Math.min(startY, currentY);
    const bottom = Math.max(startY, currentY);
    
    const px = getPixelsPerHour();
    const startMin = Math.floor((top - headerHeight) / (px / 60)) + startHour * 60;
    const endMin = Math.floor((bottom - headerHeight) / (px / 60)) + startHour * 60;
    const roundedStart = Math.floor(startMin / 15) * 15;
    const roundedEnd = Math.ceil(endMin / 15) * 15;
    
    if (roundedEnd - roundedStart < 15) {
      selectionEl.remove();
      selectionEl = null;
      return;
    }
    
    // Keep the dashed selection box visible until user picks category or types title
    selectionEl.style.pointerEvents = "none";
    selectionEl.classList.add("drag-selection-pending");
    
    const startTime = new Date(`${day}T00:00:00`);
    startTime.setMinutes(roundedStart);
    const endTime = new Date(`${day}T00:00:00`);
    endTime.setMinutes(roundedEnd);
    
    // Set pending chunk and show modal: title + category dropdown
    pendingQuickLogChunk = { start_at: startTime.toISOString(), end_at: endTime.toISOString() };
    updatePendingChunkHint();
    openDragAddModal(startTime, endTime, selectionEl);
  };
  
  col.addEventListener("mouseup", endDrag);
  col.addEventListener("mouseleave", () => {
    // Only remove selection if still dragging (not if it's pending for user to pick category)
    if (dragging && selectionEl && !selectionEl.classList.contains("drag-selection-pending")) {
      selectionEl.remove();
      selectionEl = null;
      dragging = false;
    }
  });
}

// ─────────────────────────────────────────────────────────────
// Context Menu & Edit
// ─────────────────────────────────────────────────────────────
function showContextMenu(x, y) {
  const menu = $("context-menu");
  if (!menu) return;
  menu.style.display = "";
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  // Keep menu inside viewport so Edit/Delete are not cut off (e.g. for events at 23–24)
  requestAnimationFrame(() => {
    const pad = 8;
    const menuW = menu.offsetWidth || 160;
    const menuH = menu.offsetHeight || 120;
    let left = parseInt(menu.style.left, 10) || x;
    let top = parseInt(menu.style.top, 10) || y;
    if (left + menuW + pad > window.innerWidth) left = window.innerWidth - menuW - pad;
    if (left < pad) left = pad;
    if (top + menuH + pad > window.innerHeight) top = window.innerHeight - menuH - pad;
    if (top < pad) top = pad;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  });
}

function hideContextMenu() {
  const menu = $("context-menu");
  if (menu) menu.style.display = "none";
}

function openEditModal() {
  const entry = selectedEntry || selectedPlannedEvent;
  if (!entry) return;
  
  const isPlanned = !selectedEntry;
  $("modal-title").textContent = isPlanned ? "Log This Event" : "Edit Entry";
  
  $("edit-title").value = entry.title || entry.summary || "";
  
  // Populate categories
  const select = $("edit-category");
  select.innerHTML = "";
  for (const cat of allCategories) {
    const opt = document.createElement("option");
    opt.value = cat.id;
    opt.textContent = cat.name;
    if (cat.id === entry.category_id) opt.selected = true;
    select.appendChild(opt);
  }
  
  const s = new Date(entry.start_at);
  const e = new Date(entry.end_at);
  $("edit-start").value = formatDateTimeLocal(s);
  $("edit-end").value = formatDateTimeLocal(e);
  
  $("edit-modal").style.display = "";
}

function formatDateTimeLocal(d) {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}T${hh}:${min}`;
}

async function saveEditedEntry() {
  const entry = selectedEntry || selectedPlannedEvent;
  if (!entry) return;
  
  const isPlanned = !selectedEntry;
  
  if (isPlanned) {
    // Convert planned event to a logged entry (so we hide the planned one and only show this logged one)
    setStatus("Creating log entry...");
    try {
      await apiPost("/api/quick_log", {
        category_id: $("edit-category").value,
        title: $("edit-title").value || "",
        tags: [],
        device: "web",
        source: "manual",
        start_at: new Date($("edit-start").value).toISOString(),
        end_at: new Date($("edit-end").value).toISOString(),
        planned_event_id: entry.id,
      });
      $("edit-modal").style.display = "none";
      setStatus("✓ Logged");
      Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
  } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
  } else {
    // Update existing entry
    const updates = {
      title: $("edit-title").value,
      category_id: $("edit-category").value,
      start_at: new Date($("edit-start").value).toISOString(),
      end_at: new Date($("edit-end").value).toISOString(),
    };
    
    setStatus("Saving...");
    try {
      await apiPatch(`/api/time_entries/${entry.id}`, updates);
      $("edit-modal").style.display = "none";
      setStatus("✓ Saved");
      Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
  }
}

async function deleteSelectedEntry() {
  if (!selectedEntry) {
    setStatus("Cannot delete external calendar events");
    return;
  }
  // No confirmation - user can undo with Cmd+Z
  const entryToRestore = { ...selectedEntry };
  setStatus("Deleting...");
  try {
    await apiDelete(`/api/time_entries/${selectedEntry.id}`);
    undoStack = { type: "delete", entry: entryToRestore };
    setStatus("✓ Deleted (Ctrl+Z to undo)");
    Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

async function undoLastAction() {
  if (!undoStack) {
    setStatus("Nothing to undo");
    return;
  }
  const action = undoStack;
  undoStack = null;
  try {
    if (action.type === "delete") {
      setStatus("Undoing delete...");
      await apiPost("/api/quick_log", {
        category_id: action.entry.category_id,
        title: action.entry.title || "",
        tags: action.entry.tags || [],
        device: "web",
        source: "manual",
        start_at: action.entry.start_at,
        end_at: action.entry.end_at,
      });
      setStatus("✓ Restored entry");
    } else if (action.type === "create" && action.createdId) {
      setStatus("Undoing create...");
      await apiDelete(`/api/time_entries/${action.createdId}`);
      setStatus("✓ Removed last created entry");
    } else {
      setStatus("Nothing to undo");
      return;
    }
    Promise.all([refreshWeek(), refreshAnalytics(), refreshTargetsProgress()]).then(() => { loadTrendsData(); }).catch(e => console.error("Refresh error:", e));
  } catch (e) {
    setStatus(`Undo failed: ${e.message}`);
  }
}

// ─────────────────────────────────────────────────────────────
// Analytics
// ─────────────────────────────────────────────────────────────
async function refreshAnalytics() {
  try {
    const data = await apiGet(`/api/analytics/week?start_day=${currentWeekStart}&lang=${encodeURIComponent(getAiLang())}&model=${encodeURIComponent(getAiInsightsModel())}`);
    renderAnalytics(data);
  } catch (e) {
    console.error("Analytics error:", e);
  }
}

function renderAnalytics(data) {
  if ($("stat-logged")) $("stat-logged").textContent = `${data.total_logged_hours}h`;
  if ($("stat-coverage")) $("stat-coverage").textContent = `${data.coverage_percent}%`;
  
  const chart = $("breakdown-chart");
  if (chart) {
    chart.innerHTML = "";
      for (const item of data.breakdown) {
        const seg = document.createElement("div");
        seg.className = "breakdown-segment";
        seg.style.width = `${item.percent}%`;
        seg.style.background = getCategoryColorForPanel(item.category);
        seg.style.opacity = "1";
        chart.appendChild(seg);
      }
  }
  
  const legend = $("breakdown-legend");
  if (legend) {
    legend.innerHTML = "";
    for (const item of data.breakdown.slice(0, 5)) {
      const el = document.createElement("div");
      el.className = "legend-item";
      el.innerHTML = `
        <span class="legend-dot" style="background:${getCategoryColorForPanel(item.category)}"></span>
        <span>${item.category}</span>
        <span class="legend-value">${item.hours}h</span>
      `;
      legend.appendChild(el);
    }
  }
}

// ─────────────────────────────────────────────────────────────
// Trends panel (past N days by category)
// ─────────────────────────────────────────────────────────────
const TRENDS_CATEGORIES = [
  "Work", "Learning", "Exercise", "Sleep", "Life essentials", "Social",
  "Chores", "Entertainment", "Commute", "Intimate / Quality Time",
  "Unplanned / Wasted", "Other"
];

function renderTrendsCategories() {
  const container = $("trends-categories");
  if (!container) return;
  container.innerHTML = "";
  TRENDS_CATEGORIES.forEach(cat => {
    const label = document.createElement("label");
    label.innerHTML = `<input type="checkbox" data-category="${escapeHtml(cat)}" /> ${escapeHtml(cat)}`;
    const input = label.querySelector("input");
    input.checked = ["Work", "Learning", "Sleep"].includes(cat);
    input.onchange = () => loadTrendsData();
    container.appendChild(label);
  });
}

function getTrendsDateRange() {
  const period = ($("trends-period") && $("trends-period").value) || "7";
  const end = formatDate(new Date());
  if (period === "custom") {
    const startEl = $("trends-start");
    const endEl = $("trends-end");
    const start = (startEl && startEl.value) || addDays(end, -6);
    const endVal = (endEl && endEl.value) || end;
    return { start, end: endVal };
  }
  const n = parseInt(period, 10) || 7;
  const start = addDays(end, -n + 1);
  return { start, end };
}

async function loadTrendsData() {
  const chartEl = $("trends-chart");
  const legendEl = $("trends-legend");
  if (!chartEl) return;
  const { start, end } = getTrendsDateRange();
  const period = ($("trends-period") && $("trends-period").value) || "7";
  if (period === "custom") {
    const startEl = $("trends-start");
    const endEl = $("trends-end");
    if (startEl) startEl.value = start;
    if (endEl) endEl.value = end;
  }
  const selected = [];
  document.querySelectorAll("#trends-categories input[type=checkbox]:checked").forEach(cb => {
    const cat = cb.getAttribute("data-category");
    if (cat) selected.push(cat);
  });
  chartEl.innerHTML = "<div class=\"muted\" style=\"padding:12px;text-align:center;\">Loading…</div>";
  if (legendEl) legendEl.innerHTML = "";
  try {
    const data = await apiGet(`/api/analytics/daily_breakdown?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}`);
    if (Array.isArray(data) && data.length >= 0) {
      drawTrendsChart(data, selected, chartEl, legendEl);
    } else {
      chartEl.innerHTML = "<div class=\"muted\" style=\"padding:12px;text-align:center;\">No data.</div>";
    }
  } catch (e) {
    console.error("Trends error:", e);
    chartEl.innerHTML = "<div class=\"muted\" style=\"padding:12px;text-align:center;\">Failed to load.</div>";
  }
}

function drawTrendsChart(dailyData, selectedCategories, chartEl, legendEl) {
  chartEl.innerHTML = "";
  if (!legendEl) legendEl = { innerHTML: "" };
  if (!dailyData.length) {
    chartEl.innerHTML = "<div class=\"muted\" style=\"padding:12px;text-align:center;\">No data for this range.</div>";
    return;
  }
  const dates = dailyData.map(d => d.date);
  const fewDays = dates.length < 7;
  const padding = { top: 12, right: 8, bottom: dates.length >= 7 ? 48 : 34, left: 36 };
  const w = Math.max(200, (chartEl.offsetWidth || 280) - padding.left - padding.right);
  const h = Math.max(100, (chartEl.offsetHeight || 160) - padding.top - padding.bottom);
  const allCats = [...new Set(dailyData.flatMap(d => Object.keys(d.categories || {})))];
  const catsToShow = selectedCategories.length ? selectedCategories.filter(c => allCats.includes(c)) : allCats.slice(0, 5);
  if (selectedCategories.length && !catsToShow.length) {
    chartEl.innerHTML = "<div class=\"muted\" style=\"padding:12px;text-align:center;\">No selected categories in this range. Select categories above.</div>";
    return;
  }
  const maxHours = Math.max(
    1,
    ...dailyData.flatMap(d =>
      (catsToShow.length ? catsToShow : allCats).map(c => ((d.categories || {})[c] || 0) / 60)
    )
  );
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${w + padding.left + padding.right} ${h + padding.top + padding.bottom}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  g.setAttribute("transform", `translate(${padding.left},${padding.top})`);
  // Y grid and axis
  for (let i = 0; i <= 5; i++) {
    const y = h - (i / 5) * h;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", 0);
    line.setAttribute("y1", y);
    line.setAttribute("x2", w);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "var(--border-light)");
    line.setAttribute("stroke-dasharray", "2,2");
    g.appendChild(line);
  }
  const yAxis = document.createElementNS("http://www.w3.org/2000/svg", "text");
  yAxis.setAttribute("x", -8);
  yAxis.setAttribute("y", 0);
  yAxis.setAttribute("text-anchor", "end");
  yAxis.setAttribute("dominant-baseline", "hanging");
  yAxis.setAttribute("fill", "var(--text-muted)");
  yAxis.setAttribute("font-size", "9");
  yAxis.textContent = "h";
  g.appendChild(yAxis);
  // X axis labels: < 7 days = every day + 45° slant; >= 7 days = at most 5 labels + 45° to avoid overlap
  function formatDateWithWeekday(dateStr) {
    try {
      const weekday = new Date(dateStr + "T12:00:00").toLocaleDateString("en-US", { weekday: "short" });
      return weekday + " " + dateStr.slice(5);
    } catch {
      return dateStr.slice(5);
    }
  }
  const maxLabels = 5;
  const step = fewDays ? 1 : Math.max(1, Math.ceil(dates.length / maxLabels));
  const labelY = h + 16;
  const rotDeg = step > 1 ? -45 : 0;
  dates.forEach((d, i) => {
    if (i % step !== 0 && i !== dates.length - 1) return;
    const x = (i / Math.max(1, dates.length - 1)) * w;
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", x);
    t.setAttribute("y", labelY);
    t.setAttribute("text-anchor", rotDeg ? "end" : "middle");
    t.setAttribute("fill", "var(--text-muted)");
    t.setAttribute("font-size", "10");
    t.setAttribute("transform", rotDeg ? `rotate(${rotDeg} ${x} ${labelY})` : "");
    t.textContent = formatDateWithWeekday(d);
    g.appendChild(t);
  });
  // Lines per category
  const series = catsToShow.length ? catsToShow : allCats.slice(0, 5);
  series.forEach((cat, idx) => {
    const color = getCategoryColorForPanel(cat);
    const points = dates.map((date, i) => {
      const mins = (dailyData[i].categories || {})[cat] || 0;
      const hours = mins / 60;
      const x = (i / Math.max(1, dates.length - 1)) * w;
      const y = maxHours > 0 ? h - (hours / maxHours) * h : h;
      return `${x},${y}`;
    });
    const poly = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    poly.setAttribute("points", points.join(" "));
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", color);
    poly.setAttribute("stroke-width", "2");
    poly.setAttribute("stroke-linecap", "round");
    poly.setAttribute("stroke-linejoin", "round");
    g.appendChild(poly);
  });
  svg.appendChild(g);
  chartEl.innerHTML = "";
  chartEl.appendChild(svg);
  if (legendEl && legendEl.innerHTML !== undefined) {
    legendEl.innerHTML = "";
    series.forEach(cat => {
      const span = document.createElement("span");
      span.innerHTML = `<span class="dot" style="background:${getCategoryColorForPanel(cat)}"></span> ${escapeHtml(cat)}`;
      legendEl.appendChild(span);
    });
  }
}

// ─────────────────────────────────────────────────────────────
// Insights Modal
// ─────────────────────────────────────────────────────────────
function openInsightsModal() {
  const end = formatDate(new Date());
  const start = addDays(end, -6);
  const startEl = $("insights-start");
  const endEl = $("insights-end");
  if (startEl) startEl.value = start;
  if (endEl) endEl.value = end;

  $("insights-modal").style.display = "";
  // Pass dates explicitly so we don't rely on input values being ready
  loadInsightsData(start, end);
}

async function loadInsightsData(overrideStart, overrideEnd) {
  const start = overrideStart != null ? overrideStart : ($("insights-start") && $("insights-start").value);
  const end = overrideEnd != null ? overrideEnd : ($("insights-end") && $("insights-end").value);

  if (!start || !end) {
    if ($("ins-total-hours")) $("ins-total-hours").textContent = "--";
    if ($("ins-coverage")) $("ins-coverage").textContent = "--";
    if ($("ins-productive")) $("ins-productive").textContent = "--";
    if ($("ins-wasted")) $("ins-wasted").textContent = "--";
    const tips = $("insights-tips");
    if (tips) tips.innerHTML = '<div class="insight-tip"><div class="insight-tip-text">Select a date range and click Apply.</div></div>';
    return;
  }

  if ($("ins-total-hours")) $("ins-total-hours").textContent = "…";
  if ($("insights-breakdown")) $("insights-breakdown").innerHTML = '<div class="muted" style="padding:12px;text-align:center;">Loading…</div>';
  if ($("insights-tips")) $("insights-tips").innerHTML = "";

  try {
    const data = await apiGet(`/api/analytics/range?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}&lang=${encodeURIComponent(getAiLang())}&model=${encodeURIComponent(getAiInsightsModel())}`);
    if (data && typeof data.total_logged_hours === "number") {
      renderInsights(data);
    } else {
      if ($("insights-tips")) $("insights-tips").innerHTML = '<div class="insight-tip"><div class="insight-tip-text">Unexpected response. Try again.</div></div>';
    }
  } catch (e) {
    console.error("Insights error:", e);
    if ($("ins-total-hours")) $("ins-total-hours").textContent = "--";
    if ($("ins-coverage")) $("ins-coverage").textContent = "--";
    if ($("ins-productive")) $("ins-productive").textContent = "--";
    if ($("ins-wasted")) $("ins-wasted").textContent = "--";
    if ($("insights-breakdown")) $("insights-breakdown").innerHTML = "";
    const tips = $("insights-tips");
    if (tips) tips.innerHTML = `<div class="insight-tip"><div class="insight-tip-text" style="color:var(--danger)">Error loading insights: ${escapeHtml(e.message)}</div></div>`;
  }
}

function renderInsights(data) {
  if (!data) return;
  const totalHours = data.total_logged_hours != null ? data.total_logged_hours : 0;
  const coverage = data.coverage_percent != null ? data.coverage_percent : 0;
  const productive = data.productive_hours != null ? data.productive_hours : 0;
  const wasted = data.wasted_hours != null ? data.wasted_hours : 0;
  if ($("ins-total-hours")) $("ins-total-hours").textContent = `${totalHours}h`;
  if ($("ins-coverage")) $("ins-coverage").textContent = `${coverage}%`;
  if ($("ins-productive")) $("ins-productive").textContent = `${productive}h`;
  if ($("ins-wasted")) $("ins-wasted").textContent = `${wasted}h`;
  
  // Breakdown bars
  const breakdown = $("insights-breakdown");
  if (breakdown) {
    breakdown.innerHTML = "";
    
    if (!data.breakdown || data.breakdown.length === 0) {
      breakdown.innerHTML = '<div class="muted" style="padding:12px;text-align:center;">No data for this period</div>';
    } else {
      const maxHours = Math.max(...data.breakdown.map(b => b.hours), 1);
      
      for (const item of data.breakdown) {
        const pct = (item.hours / maxHours) * 100;
        const color = getCategoryColorForPanel(item.category);
        
        const row = document.createElement("div");
        row.className = "breakdown-bar-item";
        row.innerHTML = `
          <div class="breakdown-bar-label">${item.category}</div>
          <div class="breakdown-bar-track">
            <div class="breakdown-bar-fill" style="width: ${pct}%; background-color: ${color}; opacity: 1;">
              ${item.hours}h
            </div>
          </div>
        `;
        breakdown.appendChild(row);
      }
    }
  }
  
  // Insights/tips
  const tips = $("insights-tips");
  if (tips) {
    tips.innerHTML = "";
    if (data.insights_error) {
      const errEl = document.createElement("div");
      errEl.className = "insight-tip";
      errEl.innerHTML = `<div class="insight-tip-text" style="color:var(--danger)">AI insights unavailable: ${escapeHtml(data.insights_error)}. Using rule-based insights below. Check Settings → AI features and .env.</div>`;
      tips.appendChild(errEl);
    }
    if (!data.insights || data.insights.length === 0) {
      if (!data.insights_error) tips.innerHTML = '<div class="insight-tip"><div class="insight-tip-text">Log more activities to get personalized insights!</div></div>';
    } else {
      for (const insight of data.insights) {
        const card = document.createElement("div");
        
        if (insight.type === "positive") {
          card.className = "insight-card";
          card.innerHTML = `
            <div class="insight-card-title">${insight.title}</div>
            <div class="insight-card-text">${insight.text}</div>
          `;
        } else {
          card.className = "insight-tip";
          card.innerHTML = `
            <div class="insight-tip-text"><strong>${insight.title}</strong> ${insight.text}</div>
          `;
        }
        
        tips.appendChild(card);
      }
    }
  }
}

// ─────────────────────────────────────────────────────────────
// Targets
// ─────────────────────────────────────────────────────────────
async function refreshTargetsProgress() {
  const container = $("targets-progress");
  if (!container) return;
  
  try {
    const endDate = addDays(currentWeekStart, 6);
    const data = await apiGet(`/api/targets/progress?start_date=${currentWeekStart}&end_date=${endDate}`);
    renderTargetsProgress(data);
  } catch (e) {
    container.innerHTML = '<div class="no-targets">Add targets in Settings</div>';
  }
}

function renderTargetsProgress(data) {
  const container = $("targets-progress");
  if (!container) return;
  
  if (!data.progress || data.progress.length === 0) {
    container.innerHTML = '<div class="no-targets">No targets set. Click ⚙ to add some!</div>';
    return;
  }

  container.innerHTML = "";
  for (const p of data.progress) {
    const item = document.createElement("div");
    item.className = "target-progress-item";
    // Use panel color helper so "Work" etc match calendar exactly
    const catColor = getCategoryColorForPanel(p.category);
    const typeLabel = p.target_type === "hours_per_day" ? "/day" : 
                      p.target_type === "hours_per_week" ? "/week" :
                      p.target_type === "min_hours" ? " min" : " max";
    // For hours_per_day (e.g. Sleep): show days satisfied, e.g. 2/7
    const isPerDay = p.target_type === "hours_per_day" && p.days_met != null && p.days_total != null;
    const valueText = isPerDay
      ? `${p.days_met}/${p.days_total} days`
      : `${p.actual_hours}h / ${p.expected_hours}h`;
    const barPercent = isPerDay ? (p.days_total ? (p.days_met / p.days_total) * 100 : 0) : Math.min(p.percent, 100);
    // Ensure small percentages (e.g. 1/7 = 14%) show a visible colored bar
    const minBarWidth = barPercent > 0 && barPercent < 25 ? 25 : barPercent;
    
    item.innerHTML = `
      <div class="target-progress-header">
        <span class="target-progress-label" style="color:${catColor}">${p.category} (${p.target_value}h${typeLabel})</span>
        <span class="target-progress-value ${p.status}" title="${isPerDay ? "days met" : "actual / target"}">${valueText}</span>
      </div>
      <div class="target-progress-bar">
        <div class="target-progress-fill ${p.status}" style="width: ${minBarWidth}%; background-color: ${catColor}; opacity: 1; min-width: ${barPercent > 0 ? '8px' : '0'};"></div>
      </div>
    `;
    container.appendChild(item);
  }
}

async function loadSettingsTargets() {
  const container = $("settings-targets-list");
  if (!container) return;
  
  try {
    const targets = await apiGet("/api/targets");
    
    if (!targets || targets.length === 0) {
      container.innerHTML = '<div class="no-targets">No targets yet. Add one below!</div>';
      return;
    }
    
    container.innerHTML = "";
    for (const t of targets) {
      const item = document.createElement("div");
      item.className = "target-item";
      
      const typeLabel = t.target_type === "hours_per_day" ? "hours/day" : 
                        t.target_type === "hours_per_week" ? "hours/week" :
                        t.target_type === "min_hours" ? "minimum hours" : "maximum hours";
      
      item.innerHTML = `
        <div class="target-item-info">
          <div class="target-item-cat">${t.category}</div>
          <div class="target-item-desc">${t.target_value} ${typeLabel}</div>
        </div>
        <button class="target-item-delete" data-id="${t.id}">×</button>
      `;
      
      item.querySelector(".target-item-delete").onclick = () => deleteTarget(t.id);
      container.appendChild(item);
    }
  } catch (e) {
    container.innerHTML = '<div class="no-targets">Error loading targets</div>';
  }
}

async function addTarget() {
  let category = ($("new-target-category") && $("new-target-category").value) || "";
  const type = $("new-target-type").value;
  const value = parseFloat($("new-target-value").value);
  const startDate = $("new-target-start").value || null;
  const endDate = $("new-target-end").value || null;

  if (category === "__custom__") {
    const customName = ($("new-target-category-custom") && $("new-target-category-custom").value) ? $("new-target-category-custom").value.trim() : "";
    if (!customName) {
      alert("Please enter a custom category name when using Custom.");
      return;
    }
    category = customName;
  }

  if (!value || value <= 0) {
    alert("Please enter a valid number of hours");
    return;
  }

  try {
    await apiPost("/api/targets", {
      category,
      target_type: type,
      target_value: value,
      start_date: startDate,
      end_date: endDate,
    });

    $("new-target-value").value = "";
    $("new-target-start").value = "";
    $("new-target-end").value = "";
    if ($("new-target-category-custom")) $("new-target-category-custom").value = "";

    loadSettingsTargets();
    refreshTargetsProgress();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function deleteTarget(id) {
  if (!confirm("Delete this target?")) return;
  try {
    await apiDelete(`/api/targets/${id}`);
    loadSettingsTargets();
    refreshTargetsProgress();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function applyPreset(preset) {
  const presets = {
    sleep: { category: "Sleep", target_type: "hours_per_day", target_value: 8 },
    work: { category: "Work", target_type: "max_hours", target_value: 8 },
    exercise: { category: "Exercise", target_type: "hours_per_day", target_value: 0.5 },
    learning: { category: "Learning", target_type: "hours_per_day", target_value: 2 },
    quality: { category: "Intimate / Quality Time", target_type: "hours_per_day", target_value: 1 },
  };
  
  const p = presets[preset];
  if (!p) return;
  
  try {
    await apiPost("/api/targets", p);
    loadSettingsTargets();
    refreshTargetsProgress();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

// ─────────────────────────────────────────────────────────────
// Settings Modal
// ─────────────────────────────────────────────────────────────
function populateTargetCategoryDropdown() {
  const sel = $("new-target-category");
  if (!sel) return;
  const saved = sel.value;
  sel.innerHTML = "";
  for (const cat of allCategories) {
    const opt = document.createElement("option");
    opt.value = cat.name;
    opt.textContent = cat.name;
    if (cat.color) opt.style.color = cat.color;
    sel.appendChild(opt);
  }
  const customOpt = document.createElement("option");
  customOpt.value = "__custom__";
  customOpt.textContent = "Custom (type below)";
  sel.appendChild(customOpt);
  if (saved && Array.from(sel.options).some(o => o.value === saved)) sel.value = saved;
  else if (sel.options.length) sel.value = sel.options[0].value;
  const customInput = $("new-target-category-custom");
  if (customInput) customInput.style.display = sel.value === "__custom__" ? "inline-block" : "none";
}

async function refreshCategoriesInSettings() {
  try {
    allCategories = await apiGet("/api/categories");
  } catch { allCategories = []; }
  populateTargetCategoryDropdown();
  const list = $("settings-categories-list");
  if (list && list.closest(".tab-content.active")) loadSettingsCategories();
}

function openSettingsModal() {
  $("settings-modal").style.display = "";
  loadSettingsTargets();
  refreshAIBuilderStatus();
  refreshConnections();
  refreshCategoriesInSettings();
  switchTab("targets");
}

async function refreshAIBuilderStatus() {
  const el = $("ai-builder-status-text");
  if (!el) return;
  try {
    const status = await apiGet("/api/dev/env_status");
    el.textContent = status.ai_builder_configured ? "Connected" : "Not set";
    el.className = "ai-builder-value " + (status.ai_builder_configured ? "connected" : "not-set");
  } catch {
    el.textContent = "—";
    el.className = "ai-builder-value";
  }
}

function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.querySelectorAll(".tab-content").forEach(content => {
    content.classList.toggle("active", content.id === `tab-${tabName}`);
  });
}

// Categories tab: list, add, update, delete
const DEFAULT_CATEGORY_COLORS = ["#7986cb", "#b0bec5", "#80cbc4", "#a5d6a7", "#81d4fa", "#bcaaa4", "#ce93d8", "#ffe082", "#90caf9", "#f48fb1", "#ef9a9a", "#cfd8dc"];

function loadSettingsCategories() {
  const list = $("settings-categories-list");
  if (!list) return;
  list.innerHTML = "";
  for (const cat of allCategories) {
    const row = document.createElement("div");
    row.className = "category-row";
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "8px";
    row.style.marginBottom = "8px";
    // Use stored color if set; otherwise use same palette as calendar/panels so Work, Sleep, etc. show correct color
    const color = cat.color || getCategoryColorForPanel(cat.name);
    row.innerHTML = `
      <input type="color" data-id="${cat.id}" data-name="${escapeHtml(cat.name)}" value="${color}" title="Color" style="width:28px;height:28px;padding:0;cursor:pointer;border:none;border-radius:4px" class="category-color-picker" />
      <input type="text" class="input category-name-edit" data-id="${cat.id}" value="${escapeHtml(cat.name)}" style="width:180px" />
      <button type="button" class="btn-xs category-delete" data-id="${cat.id}" data-name="${escapeHtml(cat.name)}" title="Delete (only if not used)">Delete</button>
    `;
    list.appendChild(row);
    const colorPicker = row.querySelector(".category-color-picker");
    const nameEdit = row.querySelector(".category-name-edit");
    const delBtn = row.querySelector(".category-delete");
    colorPicker.onchange = () => updateCategoryColor(cat.id, colorPicker.value);
    nameEdit.onblur = () => { const v = nameEdit.value.trim(); if (v && v !== cat.name) updateCategoryName(cat.id, v); };
    delBtn.onclick = () => deleteCategory(cat.id, cat.name);
  }
  if (allCategories.length === 0) list.innerHTML = '<div class="muted">No categories yet. Add one below.</div>';
  const hexEl = $("new-category-color-hex");
  const colorInput = $("new-category-color");
  if (hexEl && colorInput) hexEl.textContent = colorInput.value;
}

async function updateCategoryColor(id, hex) {
  try {
    await apiPatch(`/api/categories/${id}`, { color: hex });
    const c = allCategories.find(x => x.id === id);
    if (c) c.color = hex;
    populateTargetCategoryDropdown();
    refreshWeek();
    refreshAnalytics();
    refreshTargetsProgress();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function updateCategoryName(id, name) {
  try {
    await apiPatch(`/api/categories/${id}`, { name });
    const c = allCategories.find(x => x.id === id);
    if (c) c.name = name;
    loadSettingsCategories();
    populateTargetCategoryDropdown();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function deleteCategory(id, name) {
  if (!confirm(`Delete category "${name}"? This will fail if it has time entries or targets.`)) return;
  try {
    await apiDelete(`/api/categories/${id}`);
    allCategories = allCategories.filter(c => c.id !== id);
    loadSettingsCategories();
    populateTargetCategoryDropdown();
    refreshWeek();
    refreshAnalytics();
    refreshTargetsProgress();
  } catch (e) {
    alert(e.message || `Error: ${e.message}`);
  }
}

function setupCategoriesForm() {
  const nameInput = $("new-category-name");
  const colorInput = $("new-category-color");
  const hexLabel = $("new-category-color-hex");
  const addBtn = $("btn-add-category");
  if (colorInput && hexLabel) colorInput.oninput = () => { hexLabel.textContent = colorInput.value; };
  if (addBtn && nameInput) {
    addBtn.onclick = async () => {
      const name = nameInput.value.trim();
      if (!name) { alert("Enter a category name"); return; }
      const color = (colorInput && colorInput.value) ? colorInput.value : undefined;
      try {
        const created = await apiPost("/api/categories", { name, color });
        allCategories.push(created);
        allCategories.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
        nameInput.value = "";
        if (colorInput) colorInput.value = "#7986cb";
        if (hexLabel) hexLabel.textContent = "#7986cb";
        loadSettingsCategories();
        populateTargetCategoryDropdown();
      } catch (e) {
        alert(e.message || `Error: ${e.message}`);
      }
    };
  }
}

// ─────────────────────────────────────────────────────────────
// AI Planning
// ─────────────────────────────────────────────────────────────
function getAiPlanningModel() {
  return localStorage.getItem("timesense_ai_planning_model") || "gemini-2.5-pro";
}

async function getAIAdvice() {
  const goals = $("ai-goals").value.split("\n").filter(g => g.trim());
  const constraints = $("ai-constraints").value.split("\n").filter(c => c.trim());
  const model = ($("ai-planning-model") && $("ai-planning-model").value) || getAiPlanningModel();

  $("btn-get-ai-advice").disabled = true;
  $("btn-get-ai-advice").textContent = "Loading...";

  try {
    const data = await apiPost("/api/ai/planning_advice", { goals, constraints, model });
    renderAIAdvice(data);
    $("ai-results").style.display = "";
  } catch (e) {
    alert(`Error: ${e.message}`);
  } finally {
    $("btn-get-ai-advice").disabled = false;
    $("btn-get-ai-advice").textContent = "Get AI Advice";
  }
}

function renderAIAdvice(data) {
  const adviceList = $("ai-advice-list");
  const template = $("ai-template");

  if (adviceList) {
    adviceList.innerHTML = "";
    const adviceArr = data.advice || [];
    for (const a of adviceArr) {
      const item = document.createElement("div");
      item.className = "ai-advice-item";
      const area = typeof a === "object" ? (a.area || "") : "";
      const issue = typeof a === "object" ? (a.issue || "") : "";
      const suggestion = typeof a === "object" ? (a.suggestion || "") : "";
      const action = typeof a === "object" ? (a.action || "") : String(a);
      item.innerHTML = `
        <div class="ai-advice-area">${escapeHtml(area)}</div>
        <div class="ai-advice-issue">${escapeHtml(issue)}</div>
        <div class="ai-advice-suggestion">${escapeHtml(suggestion)}</div>
        <div class="ai-advice-action">💡 ${escapeHtml(action)}</div>
      `;
      adviceList.appendChild(item);
    }
    if (data.insights_error) {
      const errEl = document.createElement("div");
      errEl.className = "muted";
      errEl.style.fontSize = "12px";
      errEl.style.marginTop = "8px";
      errEl.textContent = adviceArr.length > 0
        ? "AI suggestions could not be generated; showing default tips. Try another model (e.g. gemini-2.5-pro) in the dropdown above."
        : "AI note: " + data.insights_error;
      if (adviceArr.length === 0) errEl.style.color = "var(--danger)";
      adviceList.appendChild(errEl);
    }
    if (adviceArr.length === 0 && !data.insights_error) {
      adviceList.innerHTML = '<div class="muted">Great job! No major issues found in your time usage.</div>';
    }
  }

  if (template) {
    template.innerHTML = "";
    const blocks = data.suggested_blocks || [];
    if (blocks.length > 0) {
      const section = document.createElement("div");
      section.className = "ai-template-section";
      for (const b of blocks) {
        const row = document.createElement("div");
        row.className = "ai-template-row";
        row.innerHTML = `
          <div class="ai-template-time">${escapeHtml(b.time || "")}</div>
          <div class="ai-template-activity">${escapeHtml(b.activity || "")}</div>
        `;
        section.appendChild(row);
      }
      template.appendChild(section);
    } else if (data.weekly_template && data.weekly_template.monday_to_friday) {
      const weekday = data.weekly_template.monday_to_friday;
      const section = document.createElement("div");
      section.className = "ai-template-section";
      for (const [time, activity] of Object.entries(weekday)) {
        const row = document.createElement("div");
        row.className = "ai-template-row";
        row.innerHTML = `
          <div class="ai-template-time">${escapeHtml(time)}</div>
          <div class="ai-template-activity">${escapeHtml(activity)}</div>
        `;
        section.appendChild(row);
      }
      template.appendChild(section);
    }
  }
}

// ─────────────────────────────────────────────────────────────
// Goals
// ─────────────────────────────────────────────────────────────
async function refreshGoals() {
  try {
    const goals = await apiGet("/api/goals");
    renderGoals(goals);
  } catch (e) {
    console.error("Goals error:", e);
  }
}

function renderGoals(goals) {
  const list = $("goals-list");
  if (!list) return;
  
  if (!goals || goals.length === 0) {
    list.innerHTML = '<div class="no-goals">No goals yet. Add one!</div>';
    return;
  }
  
  list.innerHTML = "";
  for (const goal of goals) {
    const item = document.createElement("div");
    item.className = "goal-item";
    
    let daysClass = "normal";
    if (goal.days_left <= 7) daysClass = "urgent";
    else if (goal.days_left <= 30) daysClass = "soon";
    
    const deadlineDate = new Date(goal.deadline);
    const deadlineStr = deadlineDate.toLocaleDateString([], { month: "short", day: "numeric" });
    
    item.innerHTML = `
      <div class="goal-info">
        <div class="goal-title">${escapeHtml(goal.title)}</div>
        <div class="goal-deadline">Due ${deadlineStr}</div>
      </div>
      <div class="goal-countdown">
        <div class="goal-days ${daysClass}">${goal.days_left}</div>
        <div class="goal-days-label">days</div>
      </div>
      <button class="goal-delete" data-id="${goal.id}">×</button>
    `;
    
    item.querySelector(".goal-delete").onclick = () => deleteGoal(goal.id);
    list.appendChild(item);
  }
}

async function saveGoal() {
  const title = $("goal-title").value.trim();
  const deadline = $("goal-deadline").value;
  
  if (!title || !deadline) {
    alert("Please fill in all fields");
    return;
  }
  
  try {
    await apiPost("/api/goals", { title, deadline });
    $("goal-modal").style.display = "none";
    $("goal-title").value = "";
    $("goal-deadline").value = "";
    refreshGoals();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

async function deleteGoal(id) {
  if (!confirm("Delete this goal?")) return;
  try {
    await apiDelete(`/api/goals/${id}`);
    refreshGoals();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

// ─────────────────────────────────────────────────────────────
// Connections
// ─────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────
// Event Review & Categorization
// ─────────────────────────────────────────────────────────────
async function refreshUncategorized() {
  try {
    const events = await apiGet("/api/events/uncategorized?limit=20");
    renderUncategorized(events);
    
    // Update badge
    const badge = $("uncategorized-count");
    if (badge) {
      badge.textContent = events.length > 0 ? events.length : "";
    }
  } catch (e) {
    console.error("Error loading uncategorized events:", e);
  }
}

// Canonical category labels for review panel (matches backend categorization sheet); icons for quick pick.
const REVIEW_CATEGORY_ICONS = {
  "Work (active)": "💼",
  "Work (passive)": "📻",
  "Learning": "📚",
  "Exercise": "💪",
  "Life essentials": "🍽️",
  "Sleep": "😴",
  "Social": "👥",
  "Chores": "🧹",
  "Entertainment": "🎬",
  "Commute": "🚌",
  "Intimate / Quality Time": "💕",
  "Unplanned / Wasted": "⏱️",
  "Other": "📌",
};

async function renderUncategorized(events) {
  const list = $("review-list");
  if (!list) return;
  
  if (!events || events.length === 0) {
    list.innerHTML = '<div class="no-items">✓ All events categorized!</div>';
    return;
  }
  
  let canonical = [];
  try {
    canonical = await apiGet("/api/categories/canonical") || [];
  } catch {
    canonical = Object.keys(REVIEW_CATEGORY_ICONS).map((name) => ({ name }));
  }
  const catNames = canonical.length ? canonical.map((c) => c.name) : Object.keys(REVIEW_CATEGORY_ICONS);

  list.innerHTML = "";
  for (const ev of events) {
    const item = document.createElement("div");
    item.className = "review-item";
    item.dataset.id = ev.id;
    
    const start = new Date(ev.start_at);
    const dateStr = start.toLocaleDateString([], { month: "short", day: "numeric" });
    const timeStr = start.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    
    item.innerHTML = `
      <div class="review-item-title">${escapeHtml(ev.title || "Untitled")}</div>
      <div class="review-item-meta">${dateStr} ${timeStr} • ${ev.calendar || ev.source}</div>
      <div class="review-item-actions" id="actions-${ev.id}"></div>
    `;
    
    const actions = item.querySelector(".review-item-actions");
    for (const name of catNames) {
      const icon = REVIEW_CATEGORY_ICONS[name] || "📌";
      const btn = document.createElement("button");
      btn.className = "cat-btn";
      btn.textContent = `${icon} ${name}`;
      btn.title = name;
      btn.onclick = () => categorizeEvent(ev.id, name);
      actions.appendChild(btn);
    }
    
    list.appendChild(item);
  }
}

async function categorizeEvent(eventId, categoryName) {
  try {
    await apiPost("/api/events/categorize", {
      event_id: eventId,
      category_name: categoryName,
    });
    
    // Remove from list
    const item = document.querySelector(`[data-id="${eventId}"]`);
    if (item) item.remove();
    
    // Update badge
    const badge = $("uncategorized-count");
    if (badge) {
      const current = parseInt(badge.textContent) || 0;
      badge.textContent = current > 1 ? current - 1 : "";
    }
    
    setStatus(`✓ Categorized as ${categoryName}`);
    refreshAnalytics();
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

async function autoCategorizAll() {
  setStatus("Auto-categorizing...");
  try {
    const result = await apiPost("/api/events/auto_categorize_all", {});
    setStatus(`✓ Categorized ${result.categorized} events (${result.needs_review} need review)`);
    refreshUncategorized();
    refreshAnalytics();
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

async function loadDayNote(day) {
  const el = $("day-note-text");
  if (!el) return;
  try {
    const data = await apiGet(`/api/day_notes?day=${encodeURIComponent(day)}`);
    el.value = data.note || "";
  } catch {
    el.value = "";
  }
}

async function saveDayNote(day) {
  const el = $("day-note-text");
  if (!el) return;
  setStatus("Saving note...");
  try {
    await apiPost(`/api/day_notes/${day}`, { note: el.value });
    setStatus("✓ Note saved");
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

function getAiLang() {
  return localStorage.getItem("timesense_ai_lang") || "en";
}

function getAiInsightsModel() {
  return localStorage.getItem("timesense_ai_insights_model") || "gemini-3-flash-preview";
}

function setAiInsightsModel(model) {
  if (!model) return;
  localStorage.setItem("timesense_ai_insights_model", model);
  const settingsSel = $("settings-ai-insights-model");
  const modalSel = $("insights-modal-model");
  if (settingsSel && settingsSel.value !== model) settingsSel.value = model;
  if (modalSel && modalSel.value !== model) modalSel.value = model;
}

function getDayReflectionModel() {
  return localStorage.getItem("timesense_ai_day_reflection_model") || "gemini-3-flash-preview";
}

function setDayReflectionModel(model) {
  if (!model) return;
  localStorage.setItem("timesense_ai_day_reflection_model", model);
  const sel = $("day-reflection-model");
  if (sel && sel.value !== model) sel.value = model;
}

async function runDayAnalysis(day) {
  const btn = $("day-note-analyze");
  const resultEl = $("day-analysis-result");
  const textEl = $("day-analysis-text");
  if (!day || !resultEl || !textEl) return;
  if (btn) { btn.disabled = true; btn.textContent = "Analyzing…"; }
  setStatus("Analyzing day (AI Builders)…");
  try {
    const lang = getAiLang();
    const model = getDayReflectionModel();
    const data = await apiGet(
      `/api/ai/day_analysis?day=${encodeURIComponent(day)}&lang=${encodeURIComponent(lang)}&model=${encodeURIComponent(model)}&debug=1`,
      { cache: "no-store" }
    );
    const analysisText = data.analysis || "No analysis returned.";
    textEl.textContent = analysisText + (data.debug_hint ? "\n\n" + data.debug_hint : "");
    resultEl.style.display = "";
    setStatus(data.error ? `Error: ${data.error}` : "✓ Analysis done");
  } catch (e) {
    textEl.textContent = `Error: ${e.message}`;
    resultEl.style.display = "";
    setStatus(`Error: ${e.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "🤖 Analyze day"; }
  }
}

async function refreshConnections() {
  try {
    const gs = await apiGet("/api/google/status");
    if ($("google-conn-status")) $("google-conn-status").textContent = gs.connected ? "Connected ✓" : "Not connected";
    if ($("btn-google")) {
      $("btn-google").textContent = gs.connected ? "Connected" : "Connect";
      $("btn-google").disabled = gs.connected;
    }
    if ($("settings-google-status")) $("settings-google-status").textContent = gs.connected ? "Connected ✓" : "Not connected";
    const settingsBtn = $("settings-btn-google");
    if (settingsBtn) {
      settingsBtn.textContent = gs.connected ? "Connected" : "Connect Google Calendar";
      settingsBtn.disabled = gs.connected;
    }
  } catch {
    if ($("google-conn-status")) $("google-conn-status").textContent = "—";
    if ($("settings-google-status")) $("settings-google-status").textContent = "—";
  }
  
  try {
    const as = await apiGet("/api/apple_sync/status");
    if ($("apple-conn-status")) $("apple-conn-status").textContent = as.connected ? `Synced (${as.total_events})` : "Not synced";
  } catch {
    if ($("apple-conn-status")) $("apple-conn-status").textContent = "—";
  }
}

async function importAppleIcs(file) {
  if (!file || !file.name) return;
  setStatus("Importing .ics…");
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch("/api/apple_calendar/ics_import", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    const n = data.imported != null ? data.imported : 0;
    setStatus(`Imported ${n} event(s) from Apple Calendar. Click ↻ to refresh.`);
    Promise.all([refreshWeek(), refreshConnections()]).catch(e => console.error(e));
  } catch (e) {
    setStatus("Import failed: " + (e.message || String(e)));
  }
}

function setupAppleIcsImport() {
  const input = $("apple-ics-input");
  const btn = $("btn-apple-ics-import");
  if (!input || !btn) return;
  btn.onclick = () => input.click();
  input.onchange = () => {
    const file = input.files && input.files[0];
    if (file) {
      importAppleIcs(file);
      input.value = "";
    }
  };
}

// ─────────────────────────────────────────────────────────────
// Push Notifications
// ─────────────────────────────────────────────────────────────
async function enablePush() {
  setStatus("Enabling push...");
  try {
    const me = await apiGet("/api/me");
    if (!me.vapid_public_key) throw new Error("VAPID not configured");
    
    const reg = await navigator.serviceWorker.register("/sw.js");
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error(`Permission: ${perm}`);
    
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(me.vapid_public_key),
    });
    
    await apiPost("/api/push/subscribe", sub);
    setStatus("✓ Push enabled");
  } catch (e) {
    setStatus(`Push error: ${e.message}`);
  }
}

async function testPush() {
  try {
    await apiPost("/api/push/test", {});
    setStatus("Test push sent");
  } catch (e) {
    setStatus(`Error: ${e.message}`);
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}

// ─────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function parseTimeToDate(timeStr, baseDate) {
  // Parse time strings like "14:30", "2:30 PM", "14:30:00"
  if (!timeStr) return null;
  
  let hours, minutes;
  const cleaned = timeStr.trim().toUpperCase();
  
  // Check for AM/PM
  const isPM = cleaned.includes("PM");
  const isAM = cleaned.includes("AM");
  const numPart = cleaned.replace(/[APM\s]/g, "");
  
  const parts = numPart.split(":");
  hours = parseInt(parts[0]);
  minutes = parts.length > 1 ? parseInt(parts[1]) : 0;
  
  if (isNaN(hours) || isNaN(minutes)) return null;
  
  // Handle AM/PM
  if (isPM && hours < 12) hours += 12;
  if (isAM && hours === 12) hours = 0;
  
  // Create date with same day as base
  const result = new Date(baseDate);
  result.setHours(hours, minutes, 0, 0);
  return result;
}

// Initialize
init().catch(e => {
  console.error("Init error:", e);
  setStatus(`Init error: ${e.message}`);
});
