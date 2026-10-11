/* Shared helpers for the dashboard. Vanilla JavaScript, no dependencies. */
(function () {
  "use strict";

  var REFRESH_MS = 5000;

  function fetchJSON(path) {
    return fetch(path, {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () { return null; })
          .then(function (body) {
            if (!response.ok) {
              var message = body && body.error
                ? body.error
                : "Request failed (HTTP " + response.status + ")";
              var error = new Error(message);
              error.status = response.status;
              // Session expired or signed out: return to the login page.
              if (response.status === 401) {
                window.location.assign("/login");
              }
              throw error;
            }
            return body;
          });
      });
  }

  function parseTimestamp(value) {
    if (!value) return null;
    var date = new Date(value);
    return isNaN(date.getTime()) ? null : date;
  }

  function formatLocal(value) {
    var date = parseTimestamp(value);
    return date ? date.toLocaleString() : "Unknown time";
  }

  function utcLabel(value) {
    var date = parseTimestamp(value);
    return date ? "UTC: " + date.toISOString() : "";
  }

  function relativeTime(value) {
    var date = parseTimestamp(value);
    if (!date) return "unknown";
    var seconds = Math.round((Date.now() - date.getTime()) / 1000);
    if (seconds < 0) seconds = 0;
    if (seconds < 60) return seconds + "s ago";
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + "m ago";
    var hours = Math.round(minutes / 60);
    if (hours < 24) return hours + "h ago";
    return Math.round(hours / 24) + "d ago";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function setText(id, text) {
    var node = document.getElementById(id);
    if (node) node.textContent = text;
  }

  function show(id, visible) {
    var node = document.getElementById(id);
    if (node) node.hidden = !visible;
  }

  function showError(id, message) {
    var node = document.getElementById(id);
    if (!node) return;
    if (message) {
      node.textContent = message;
      node.hidden = false;
    } else {
      node.textContent = "";
      node.hidden = true;
    }
  }

  function setServerStatus(state, text) {
    var node = document.getElementById("server-status");
    if (!node) return;
    node.className = "status-pill status-pill--" + state;
    node.textContent = text;
  }

  var TYPE_LABELS = { connected: "Connected", disconnected: "Disconnected" };
  var STATUS_LABELS = {
    recent: "Heartbeat OK",
    stale: "No recent heartbeat",
    never: "No heartbeat yet"
  };
  var STATUS_BADGE = { recent: "online", stale: "stale", never: "never" };

  function typeLabel(type) {
    return TYPE_LABELS[type] || (type || "unknown");
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || "Unknown";
  }

  function statusBadgeClass(status) {
    return "badge badge--" + (STATUS_BADGE[status] || "never");
  }

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  window.dashboardCommon = {
    REFRESH_MS: REFRESH_MS,
    fetchJSON: fetchJSON,
    formatLocal: formatLocal,
    utcLabel: utcLabel,
    relativeTime: relativeTime,
    el: el,
    setText: setText,
    show: show,
    showError: showError,
    setServerStatus: setServerStatus,
    typeLabel: typeLabel,
    statusLabel: statusLabel,
    statusBadgeClass: statusBadgeClass,
    clearChildren: clearChildren
  };
})();
