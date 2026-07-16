// Apply the saved theme before first paint to avoid a flash.
(function () {
  try {
    var t = localStorage.getItem("pb-theme") || "dark";
    document.documentElement.setAttribute("data-theme", t);
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();
