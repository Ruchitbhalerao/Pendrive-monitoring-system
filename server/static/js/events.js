/* Event history: filters, pagination, and auto-refresh. */
(function () {
  "use strict";

  var c = window.dashboardCommon;
  var inFlight = false;
  var totalPages = 1;

  var state = {
    page: 1,
    per_page: 20,
    computer_id: "",
    event_type: "",
    device_category: "",
    q: ""
  };

  function buildQuery() {
    var params = new URLSearchParams();
    params.set("page", String(state.page));
    params.set("per_page", String(state.per_page));
    if (state.computer_id) params.set("computer_id", state.computer_id);
    if (state.event_type) params.set("event_type", state.event_type);
    if (state.device_category) params.set("device_category", state.device_category);
    if (state.q) params.set("q", state.q);
    return params;
  }

  function syncUrl() {
    history.replaceState(null, "", "/events?" + buildQuery().toString());
  }

  function readStateFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var page = parseInt(params.get("page"), 10);
    var perPage = parseInt(params.get("per_page"), 10);
    state.page = page > 0 ? page : 1;
    state.per_page = [20, 50, 100].indexOf(perPage) >= 0 ? perPage : 20;
    state.computer_id = params.get("computer_id") || "";
    state.event_type = params.get("event_type") || "";
    state.device_category = params.get("device_category") || "";
    state.q = params.get("q") || "";
  }

  function readStateFromForm() {
    state.computer_id = document.getElementById("filter-computer").value.trim();
    state.event_type = document.getElementById("filter-type").value;
    state.device_category = document.getElementById("filter-category").value;
    state.q = document.getElementById("filter-search").value.trim();
    state.per_page = parseInt(document.getElementById("filter-per-page").value, 10) || 20;
  }

  function fillForm() {
    document.getElementById("filter-computer").value = state.computer_id;
    document.getElementById("filter-type").value = state.event_type;
    document.getElementById("filter-category").value = state.device_category;
    document.getElementById("filter-search").value = state.q;
    document.getElementById("filter-per-page").value = String(state.per_page);
  }

  function deviceText(event) {
    var parts = [];
    if (event.device_name) parts.push(event.device_name);
    if (event.vendor_id || event.product_id) {
      parts.push("ID " + (event.vendor_id || "----") + ":" + (event.product_id || "----"));
    }
    return parts.length ? parts.join(" \u2014 ") : "Unnamed device";
  }

  function renderRows(events) {
    var body = document.getElementById("events-body");
    c.clearChildren(body);
    c.show("events-empty", events.length === 0);

    events.forEach(function (event) {
      var row = document.createElement("tr");

      var timeCell = c.el("td", null, c.formatLocal(event.occurred_at));
      timeCell.title = c.utcLabel(event.occurred_at);
      row.appendChild(timeCell);

      row.appendChild(c.el("td", null, event.computer_id));

      var typeCell = document.createElement("td");
      typeCell.appendChild(
        c.el("span", "badge badge--" + event.event_type, c.typeLabel(event.event_type))
      );
      row.appendChild(typeCell);

      row.appendChild(c.el("td", null, event.device_category || "unknown"));
      row.appendChild(c.el("td", null, deviceText(event)));

      body.appendChild(row);
    });
  }

  function renderPagination(data) {
    totalPages = data.total_pages;
    var container = document.getElementById("pagination");
    var status = document.getElementById("page-status");
    var prev = document.getElementById("page-prev");
    var next = document.getElementById("page-next");

    var currentPage = data.total_pages === 0 ? 1 : data.page;
    var shownPages = data.total_pages === 0 ? 1 : data.total_pages;
    status.textContent = "Page " + currentPage + " of " + shownPages +
      " (" + data.total + " event" + (data.total === 1 ? "" : "s") + ")";

    prev.disabled = data.page <= 1;
    next.disabled = data.total_pages === 0 || data.page >= data.total_pages;
    container.hidden = false;
  }

  function load() {
    if (inFlight) return;
    inFlight = true;

    c.fetchJSON("/api/events?" + buildQuery().toString())
      .then(function (data) {
        renderRows(data.events);
        renderPagination(data);
        c.showError("events-error", null);
        c.setText("events-refresh", "Last updated " + new Date().toLocaleTimeString());
        document.getElementById("results-count").textContent =
          data.total + " matching event" + (data.total === 1 ? "" : "s");
        c.setServerStatus("online", "Server online");
      })
      .catch(function (error) {
        c.showError("events-error", "Could not load events: " + error.message);
        c.setServerStatus("error", "Server or database problem");
      })
      .then(function () {
        inFlight = false;
      });
  }

  function goToPage(page) {
    if (page < 1 || (totalPages > 0 && page > totalPages)) return;
    state.page = page;
    syncUrl();
    load();
  }

  document.addEventListener("DOMContentLoaded", function () {
    readStateFromUrl();
    fillForm();

    document.getElementById("filters-form").addEventListener("submit", function (event) {
      event.preventDefault();
      readStateFromForm();
      state.page = 1;
      syncUrl();
      load();
    });

    document.getElementById("clear-filters").addEventListener("click", function () {
      state = {
        page: 1, per_page: 20,
        computer_id: "", event_type: "", device_category: "", q: ""
      };
      fillForm();
      syncUrl();
      load();
    });

    document.getElementById("page-prev").addEventListener("click", function () {
      goToPage(state.page - 1);
    });
    document.getElementById("page-next").addEventListener("click", function () {
      goToPage(state.page + 1);
    });

    load();
    setInterval(load, c.REFRESH_MS);
  });
})();
