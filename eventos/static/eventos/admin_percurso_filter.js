(function () {
  function onReady(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback);
      return;
    }
    callback();
  }

  onReady(function () {
    var corridaField = document.getElementById("id_corrida");
    if (!corridaField) {
      return;
    }

    corridaField.addEventListener("change", function () {
      var corridaId = corridaField.value;
      var url = new URL(window.location.href);

      if (corridaId) {
        url.searchParams.set("corrida", corridaId);
      } else {
        url.searchParams.delete("corrida");
      }

      window.location.href = url.toString();
    });
  });
})();
