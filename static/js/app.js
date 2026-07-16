// PayBridge dashboard interactions: sidebar, theme toggle, chart, confirms.
(function () {
  "use strict";

  // ---- Mobile sidebar -----------------------------------------------------
  const sidebar = document.querySelector(".pb-sidebar");
  const backdrop = document.getElementById("pb-backdrop");
  const toggle = document.getElementById("pb-menu-toggle");
  function openSidebar() { sidebar && sidebar.classList.add("show"); backdrop && backdrop.classList.add("show"); }
  function closeSidebar() { sidebar && sidebar.classList.remove("show"); backdrop && backdrop.classList.remove("show"); }
  toggle && toggle.addEventListener("click", openSidebar);
  backdrop && backdrop.addEventListener("click", closeSidebar);

  // ---- Theme toggle -------------------------------------------------------
  const themeBtn = document.getElementById("pb-theme-toggle");
  themeBtn && themeBtn.addEventListener("click", function () {
    const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    const next = cur === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("pb-theme", next); } catch (e) {}
    const icon = themeBtn.querySelector("i");
    if (icon) icon.className = next === "light" ? "bi bi-moon-stars" : "bi bi-sun";
  });

  // ---- Confirm-before-submit ---------------------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // ---- Overview volume chart ---------------------------------------------
  const el = document.getElementById("volumeChart");
  if (el && window.Chart) {
    const labels = JSON.parse(el.dataset.labels || "[]");
    const values = JSON.parse(el.dataset.values || "[]");
    const ctx = el.getContext("2d");
    const grad = ctx.createLinearGradient(0, 0, 0, 260);
    grad.addColorStop(0, "rgba(109,94,252,.45)");
    grad.addColorStop(1, "rgba(109,94,252,0)");
    new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: "Succeeded volume",
          data: values,
          borderColor: "#8b5cf6",
          backgroundColor: grad,
          borderWidth: 2,
          fill: true,
          tension: 0.38,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: "#8b5cf6",
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { color: "#64748b", maxRotation: 0, autoSkip: true } },
          y: { grid: { color: "rgba(148,163,184,.1)" }, ticks: { color: "#64748b" }, beginAtZero: true },
        },
      },
    });
  }

  // ---- Auto-submit filter selects ----------------------------------------
  document.querySelectorAll("[data-autosubmit]").forEach(function (sel) {
    sel.addEventListener("change", function () { sel.form && sel.form.submit(); });
  });
})();
