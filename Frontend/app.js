const API = "/api";

const state = {
  q: "",
  category: "",
  mode: "",
  sort: "recent",
  upcoming: false,
  loading: false,
  bookmarked_only: false,
  date: "", 
};

const el = {
  grid: document.getElementById("eventsGrid"),
  empty: document.getElementById("emptyState"),
  search: document.getElementById("searchInput"),
  category: document.getElementById("categoryFilter"),
  mode: document.getElementById("modeFilter"),
  sort: document.getElementById("sortFilter"),
  upcoming: document.getElementById("upcomingFilter"),
  scrapeBtn: document.getElementById("scrapeBtn"),
  demoBtn: document.getElementById("demoBtn"),
  addBtn: document.getElementById("addBtn"),
  modal: document.getElementById("modalBackdrop"),
  form: document.getElementById("eventForm"),
  cancelBtn: document.getElementById("cancelBtn"),
  toast: document.getElementById("toast"),
  bookmark: document.getElementById("bookmarkFilter"),
  date: document.getElementById("dateFilter"),
};

/* ------------------------------------------------------------------ */
/* Utilities                                                           */
/* ------------------------------------------------------------------ */
function toast(message, type = "") {
  el.toast.textContent = message;
  el.toast.className = `toast ${type}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.toast.classList.add("hidden"), 3800);
}

// Backend stores naive UTC datetimes; append "Z" if there is no offset.
function parseDate(value) {
  if (!value) return null;
  const hasZone = /Z$|[+-]\d{2}:?\d{2}$/.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function deadlineLabel(value) {
  const date = parseDate(value);
  if (!date) return { text: "No deadline", cls: "" };
  const days = Math.ceil((date - new Date()) / 86_400_000);
  if (days < 0) return { text: "Closed", cls: "urgent" };
  if (days === 0) return { text: "Closes today", cls: "urgent" };
  if (days <= 3) return { text: `${days} day${days > 1 ? "s" : ""} left`, cls: "urgent" };
  return { text: `${days} days left`, cls: "ok" };
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

/* ------------------------------------------------------------------ */
/* API calls                                                           */
/* ------------------------------------------------------------------ */
async function apiFetch(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || `Request failed (${response.status})`);
  }
  return data;
}

async function loadStats() {
  try {
    const stats = await apiFetch("/stats");
    document.getElementById("statTotal").textContent = stats.total_events;
    document.getElementById("statUpcoming").textContent = stats.upcoming_events;
    document.getElementById("statHackathons").textContent = stats.by_category.hackathon || 0;
    document.getElementById("statSources").textContent = Object.keys(stats.by_source).length;
  } catch (error) {
    console.error("Stats failed:", error);
  }
}

async function loadEvents() {
  if (state.loading) return;
  state.loading = true;
  el.grid.innerHTML = `<p class="empty">Loading events…</p>`;

  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.category) params.set("category", state.category);
  if (state.mode) params.set("mode", state.mode);
  if (state.upcoming) params.set("upcoming_only", "true");
  params.set("sort", state.sort);
  params.set("limit", "60");

  try {
    const data = await apiFetch(`/events?${params.toString()}`);
    renderEvents(data.items);
  } catch (error) {
    el.grid.innerHTML = "";
    el.empty.classList.remove("hidden");
    el.empty.textContent = `Could not load events: ${error.message}`;
    toast(error.message, "error");
  } finally {
    state.loading = false;
  }
}

/* ------------------------------------------------------------------ */
/* Rendering                                                           */
/* ------------------------------------------------------------------ */
function renderEvents(events) {
  // 1. Local storage se bookmarks uthao
  const bookmarks = JSON.parse(localStorage.getItem('bookmarks') || '[]');

  // 2. Agar filter ON hai, to sirf bookmarks wale events dikhao
  if (state.bookmarked_only) {
    events = events.filter(e => bookmarks.includes(e.url));
  }
  // --- DATE FILTER LOGIC ---
  if (state.date) {
    const selectedTime = new Date(state.date).getTime();
    
    events = events.filter(e => {
      if (!e.starts_at) return false;
      
      // Timezone issue se bachne ke liye sirf date ('YYYY-MM-DD') extract kar rahe hain
      const start = new Date(e.starts_at.split('T')[0]).getTime();
      const end = e.deadline ? new Date(e.deadline.split('T')[0]).getTime() : start;
      
      // Agar select ki hui date start aur deadline ke beech me hai, toh event dikhao
      return selectedTime >= start && selectedTime <= end;
    });
  }
  // --------------------------

  if (!events.length) {
    el.grid.innerHTML = "";
    el.empty.classList.remove("hidden");
    return;
  }
  el.empty.classList.add("hidden");

  el.grid.innerHTML = events
    .map((event) => {
      const deadline = deadlineLabel(event.deadline);
      // Check karo ki kya is event ka URL bookmarks array me hai
      const isMarked = bookmarks.includes(event.url);
      const safeUrl = encodeURIComponent(event.url);
      const safeTitleForAI = encodeURIComponent(event.title + " tech event dark neon background");
      const aiImageUrl = `https://image.pollinations.ai/prompt/${safeTitleForAI}?width=600&height=300&nologo=true`;

      return `
        <article class="card">
          <!-- YE NAYI AI IMAGE HAI -->
          <img src="${aiImageUrl}" alt="${escapeHtml(event.title)}" class="card-image" loading="lazy" />
          <div class="card-head">
            <h3><a href="${escapeHtml(event.url)}" target="_blank" rel="noopener noreferrer">
              ${escapeHtml(event.title)}
            </a></h3>
            <!-- BOOKMARK BUTTON -->
            <button onclick="toggleBookmark(decodeURIComponent('${safeUrl}'))" class="bookmark-btn ${isMarked ? 'active' : ''}" title="Bookmark this event">
              ${isMarked ? '★' : '☆'}
            </button>
          </div>

          <div class="meta">
            <span class="pill">${escapeHtml(event.category)}</span>
            <span class="pill">${escapeHtml(event.mode)}</span>
            <span class="pill source">${escapeHtml(event.source)}</span>
          </div>

          <p class="desc">${escapeHtml((event.description || "No description provided.").slice(0, 180))}</p>

          <div class="meta">
            ${event.organizer ? `<span>🏢 ${escapeHtml(event.organizer)}</span>` : ""}
            ${event.location ? `<span>📍 ${escapeHtml(event.location)}</span>` : ""}
          </div>

          <div class="card-foot">
            <span>🗓 ${formatDate(event.starts_at)}</span>
            <div>
              <span class="${deadline.cls}">${deadline.text}</span>
              <button onclick="confirmDelete(${event.id})" class="delete-btn" title="Delete Event">🗑️</button>
            </div>
          </div>
        </article>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/* Actions                                                             */
/* ------------------------------------------------------------------ */
async function handleScrape(simulateNew) {
  const button = simulateNew ? el.demoBtn : el.scrapeBtn;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "⏳ Working…";

  try {
    const result = await apiFetch(`/scrape?simulate_new=${simulateNew}&notify=true`, {
      method: "POST",
    });

    if (result.events_added === 0) {
      toast(`No new events. ${result.events_skipped} already known.`);
    } else {
      toast(
        `Added ${result.events_added} new event(s) • ${result.notifications_sent} Discord ping(s)`,
        "success"
      );
    }
    if (result.errors.length) console.warn("Scraper errors:", result.errors);

    await Promise.all([loadEvents(), loadStats()]);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

let eventToDelete = null;

// Ye function box ko open karega
function confirmDelete(id) {
  eventToDelete = id;
  document.getElementById("deleteModalBackdrop").classList.remove("hidden");
}

// Ye function box ko band karega
function closeDeleteModal() {
  eventToDelete = null;
  document.getElementById("deleteModalBackdrop").classList.add("hidden");
}

// Ye function actual me backend se delete karega
async function executeDelete() {
  if (!eventToDelete) return;
  const id = eventToDelete;
  closeDeleteModal(); // Delete click hote hi pehle box band karo
  
  try {
    await apiFetch(`/events/${id}`, { method: "DELETE" });
    toast("Event deleted successfully!", "success");
    await Promise.all([loadEvents(), loadStats()]);
  } catch (error) {
    toast(error.message, "error");
  }
}

window.confirmDelete = confirmDelete;
window.toggleBookmark = function(url) {
  let bookmarks = JSON.parse(localStorage.getItem('bookmarks') || '[]');
  
  if (bookmarks.includes(url)) {
    // Agar pehle se hai, to hata do
    bookmarks = bookmarks.filter(b => b !== url);
  } else {
    // Nahi hai to add kar do
    bookmarks.push(url);
  }
  
  localStorage.setItem('bookmarks', JSON.stringify(bookmarks));
  loadEvents(); // UI ko refresh karne ke liye
}

// Buttons ko event listeners ke sath jodna
document.getElementById("cancelDeleteBtn").addEventListener("click", closeDeleteModal);
document.getElementById("confirmDeleteBtn").addEventListener("click", executeDelete);
document.getElementById("deleteModalBackdrop").addEventListener("click", (e) => {
  if (e.target.id === "deleteModalBackdrop") closeDeleteModal();
});

async function handleSubmit(formEvent) {
  formEvent.preventDefault();
  const formData = new FormData(el.form);
  const payload = Object.fromEntries(formData.entries());

  Object.keys(payload).forEach((key) => {
    if (payload[key] === "") delete payload[key];
  });

  // <input type="date"> gives "YYYY-MM-DD" — expand to full ISO datetime.
  ["starts_at", "deadline"].forEach((key) => {
    if (payload[key]) payload[key] = `${payload[key]}T23:59:00`;
  });

  if (!payload.url || !payload.title) {
    toast("Title and URL are required.", "error");
    return;
  }

  try {
    await apiFetch("/events", { method: "POST", body: JSON.stringify(payload) });
    toast("Event added — Discord notified!", "success");
    closeModal();
    el.form.reset();
    await Promise.all([loadEvents(), loadStats()]);
  } catch (error) {
    toast(error.message, "error");
  }
}

function openModal() {
  el.modal.classList.remove("hidden");
}

function closeModal() {
  el.modal.classList.add("hidden");
}

/* ------------------------------------------------------------------ */
/* Event wiring (AI Semantic Search)                                   */
/* ------------------------------------------------------------------ */
el.search.addEventListener("keypress", async (e) => {
  if (e.key === "Enter") {
    const query = e.target.value.trim();
    
    // Agar search bar khali hai, toh saare events wapas le aao
    if (!query) {
      state.q = "";
      loadEvents();
      return;
    }

    // AI Loading State
    el.grid.innerHTML = `<p style="text-align:center; width:100%; color:#a855f7;">✨ AI is scanning events context... please wait.</p>`;
    
    try {
      const res = await fetch(`/api/ai-search?query=${encodeURIComponent(query)}`);
      if (res.ok) {
        let aiEvents = await res.json();
        
        // Tumhara date/bookmark filter yahan bhi kaam karega
        const bookmarks = JSON.parse(localStorage.getItem('bookmarks') || '[]');
        if (state.bookmarked_only) {
          aiEvents = aiEvents.filter(e => bookmarks.includes(e.url));
        }
        if (state.date) {
          const selectedTime = new Date(state.date).getTime();
          aiEvents = aiEvents.filter(e => {
            if (!e.starts_at) return false;
            const start = new Date(e.starts_at.split('T')[0]).getTime();
            const end = e.deadline ? new Date(e.deadline.split('T')[0]).getTime() : start;
            return selectedTime >= start && selectedTime <= end;
          });
        }
        
        renderEvents(aiEvents);
      } else {
        throw new Error("Server returned " + res.status);
      }
    } catch (err) {
      console.error("AI Search Failed", err);
      el.grid.innerHTML = `<p class="empty" style="color:red;">AI API Error. Backend model load ho raha hai ya server band hai.</p>`;
    }
  }
});

el.category.addEventListener("change", (e) => { state.category = e.target.value; loadEvents(); });
el.mode.addEventListener("change", (e) => { state.mode = e.target.value; loadEvents(); });
el.sort.addEventListener("change", (e) => { state.sort = e.target.value; loadEvents(); });
el.upcoming.addEventListener("change", (e) => { state.upcoming = e.target.checked; loadEvents(); });

el.scrapeBtn.addEventListener("click", () => handleScrape(false));
el.demoBtn.addEventListener("click", () => handleScrape(true));
el.addBtn.addEventListener("click", openModal);
el.cancelBtn.addEventListener("click", closeModal);
el.modal.addEventListener("click", (e) => { if (e.target === el.modal) closeModal(); });
el.form.addEventListener("submit", handleSubmit);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
el.bookmark.addEventListener("change", (e) => { state.bookmarked_only = e.target.checked; loadEvents(); });
el.date.addEventListener("change", (e) => { state.date = e.target.value; loadEvents(); });

/* ------------------------------------------------------------------ */
/* Boot                                                                */
/* ------------------------------------------------------------------ */
(async function init() {
  await Promise.all([loadEvents(), loadStats()]);
})();

/* ------------------------------------------------------------------ */
/* Chatbot Logic                                                       */
/* ------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", () => {
  const chatToggleBtn = document.getElementById("chatToggleBtn");
  const chatWidget = document.getElementById("chatWidget");
  const closeChatBtn = document.getElementById("closeChatBtn");
  const chatBody = document.getElementById("chatBody");
  const chatInputMsg = document.getElementById("chatInputMsg");
  const sendChatBtn = document.getElementById("sendChatBtn");

  if(!chatToggleBtn) return; // Agar HTML load nahi hua toh error nahi aayega

  chatToggleBtn.addEventListener("click", () => chatWidget.classList.toggle("hidden"));
  closeChatBtn.addEventListener("click", () => chatWidget.classList.add("hidden"));

  async function handleSendMessage() {
    const text = chatInputMsg.value.trim();
    if (!text) return;

    chatBody.innerHTML += `<div class="user-msg">${escapeHtml(text)}</div>`;
    chatInputMsg.value = "";
    chatBody.scrollTop = chatBody.scrollHeight; 

    const loadingId = "load-" + Date.now();
    chatBody.innerHTML += `<div class="bot-msg" id="${loadingId}">Typing... ✨</div>`;
    chatBody.scrollTop = chatBody.scrollHeight;

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      });
      const data = await res.json();
      
      document.getElementById(loadingId).remove();
      chatBody.innerHTML += `<div class="bot-msg">${data.reply.replace(/\n/g, '<br>')}</div>`;
    } catch (err) {
      document.getElementById(loadingId).remove();
      chatBody.innerHTML += `<div class="bot-msg" style="color:red;">Network Error!</div>`;
    }
    chatBody.scrollTop = chatBody.scrollHeight;
  }

  sendChatBtn.addEventListener("click", handleSendMessage);
  chatInputMsg.addEventListener("keypress", (e) => {
    if (e.key === "Enter") handleSendMessage();
  });
});