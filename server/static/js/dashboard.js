/* Main dashboard: summary cards, monitored computers, recent alerts. */
(function () {
  "use strict";

  var c = window.dashboardCommon;
  var inFlight = false;
  var staleAfter = 90;

  function refreshLabel(id) {
    c.setText(id, "Last updated " + new Date().toLocaleTimeString());
  }

  function settleAll(promises) {
    if (typeof Promise.allSettled === "function") {
      return Promise.allSettled(promises);
    }
    return Promise.all(promises.map(function (promise) {
      return promise.then(
        function (value) { return { status: "fulfilled", value: value }; },
        function (reason) { return { status: "rejected", reason: reason }; }
      );
    }));
  }

  function renderSummary(summary) {
    c.setText("card-total", summary.total_computers);
    c.setText("card-online", summary.online);
    c.setText("card-stale", summary.stale);
    c.setText("card-events", summary.total_events);

    var rule = document.getElementById("status-rule");
    if (rule) {
      rule.hidden = false;
      rule.textContent =
        "Online = a client heartbeat arrived within the last " + staleAfter +
        " seconds. Stale = last heartbeat older than that. No heartbeat yet = " +
        "the client has never sent one (" + summary.never_reported + "). " +
        "Status reflects live client health; USB event activity is shown " +
        "separately under Last activity.";
    }
  }

  function renderComputers(computers) {
    var body = document.getElementById("computers-body");
    c.clearChildren(body);
    c.show("computers-empty", computers.length === 0);

    computers.forEach(function (computer) {
      var row = document.createElement("tr");

      row.appendChild(c.el("td", null, computer.computer_id));

      var heartbeatCell = c.el("td", null,
        computer.last_heartbeat
          ? c.relativeTime(computer.last_heartbeat) + " \u2014 " + c.formatLocal(computer.last_heartbeat)
          : "Never");
      heartbeatCell.title = c.utcLabel(computer.last_heartbeat);
      row.appendChild(heartbeatCell);

      var activity = computer.last_event_at || computer.last_seen;
      var lastCell = c.el("td", null, c.formatLocal(activity));
      lastCell.title = c.utcLabel(activity);
      row.appendChild(lastCell);

      row.appendChild(c.el("td", null, String(computer.event_count)));

      var statusCell = document.createElement("td");
      statusCell.appendChild(
        c.el("span", c.statusBadgeClass(computer.status), c.statusLabel(computer.status))
      );
      row.appendChild(statusCell);

      var actionCell = document.createElement("td");
      var link = document.createElement("a");
      link.href = "/events?computer_id=" + encodeURIComponent(computer.computer_id);
      link.textContent = "View events";
      actionCell.appendChild(link);
      row.appendChild(actionCell);

      body.appendChild(row);
    });
  }

  function renderAlerts(events) {
    var list = document.getElementById("alerts-list");
    c.clearChildren(list);
    c.show("alerts-empty", events.length === 0);

    events.slice(0, 10).forEach(function (event) {
      var item = document.createElement("li");
      item.className = "alert-item alert-item--" + event.event_type;

      item.appendChild(
        c.el("span", "badge badge--" + event.event_type, c.typeLabel(event.event_type))
      );

      var details = document.createElement("div");
      details.appendChild(c.el("div", "alert-item__device", event.device_name || "Unnamed device"));
      var meta = [event.computer_id, event.device_category || "unknown"];
      details.appendChild(c.el("div", "alert-item__meta", meta.join(" \u00b7 ")));
      item.appendChild(details);

      var timeCell = c.el("div", "alert-item__time",
        c.relativeTime(event.occurred_at) + " \u2014 " + c.formatLocal(event.occurred_at));
      timeCell.title = c.utcLabel(event.occurred_at);
      item.appendChild(timeCell);

      list.appendChild(item);
    });
  }

  function doRefresh() {
    if (inFlight) return;
    inFlight = true;

    var jobs = [c.fetchJSON("/api/computers"), c.fetchJSON("/api/events?per_page=10")];

    settleAll(jobs).then(function (results) {
      var computersResult = results[0];
      var eventsResult = results[1];
      var anyError = false;

      if (computersResult.status === "fulfilled") {
        staleAfter = computersResult.value.stale_after_seconds;
        renderSummary(computersResult.value.summary);
        renderComputers(computersResult.value.computers);
        c.showError("computers-error", null);
        refreshLabel("computers-refresh");
      } else {
        anyError = true;
        c.showError("computers-error",
          "Could not load computers: " + computersResult.reason.message);
      }

      if (eventsResult.status === "fulfilled") {
        renderAlerts(eventsResult.value.events);
        c.showError("alerts-error", null);
        refreshLabel("alerts-refresh");
      } else {
        anyError = true;
        c.showError("alerts-error",
          "Could not load events: " + eventsResult.reason.message);
      }

      c.setServerStatus(anyError ? "error" : "online",
        anyError ? "Server or database problem" : "Server online");
      inFlight = false;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    doRefresh();
    setInterval(doRefresh, c.REFRESH_MS);
  });
})();
