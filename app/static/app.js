"use strict";

let activeCvControl = null;

function openDialog(dialogId) {
  const dialog = document.getElementById(dialogId);
  if (dialog && !dialog.open) {
    dialog.showModal();
  }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) {
    return;
  }
  const dialogId = target.dataset.closeDialog;
  if (dialogId) {
    document.getElementById(dialogId)?.close();
    return;
  }
  if (target.dataset.openCvPicker !== undefined) {
    activeCvControl = target.closest(".cv-control");
    openDialog("cv-picker-dialog");
    window.htmx.ajax("GET", "/cv-picker", "#cv-picker-content");
    return;
  }
  if (target.dataset.openApplicationDialog !== undefined) {
    openDialog("application-dialog");
    window.htmx.ajax("GET", "/applications/new", "#application-dialog-content");
    return;
  }
  if (target.dataset.cvUri && activeCvControl) {
    activeCvControl.querySelector("input[name='cv_path']").value =
      target.dataset.cvUri;
    activeCvControl.querySelector("[data-cv-name]").textContent =
      target.dataset.cvName;
    document.getElementById("cv-picker-dialog").close();
    return;
  }
  if (target.dataset.previewUrl) {
    document.getElementById("cv-preview-frame").src = target.dataset.previewUrl;
    openDialog("cv-preview-dialog");
  }
});
