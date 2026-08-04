"use strict";

function openDialog(dialogId) {
  const dialog = document.getElementById(dialogId);
  if (dialog && !dialog.open) {
    dialog.showModal();
  }
}

function closeDialog(dialogId) {
  const dialog = document.getElementById(dialogId);
  if (dialog?.open) {
    dialog.close();
  }
  if (dialogId === "cv-preview-dialog") {
    document.getElementById("cv-preview-frame").src = "";
  }
}

document
  .getElementById("cv-preview-dialog")
  ?.addEventListener("close", () => {
    document.getElementById("cv-preview-frame").src = "";
  });

document.body.addEventListener("close-stage-editor", () => {
  closeDialog("stage-editor-dialog");
});

document.body.addEventListener("close-notes-editor", () => {
  closeDialog("notes-editor-dialog");
});

document.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target) {
    return;
  }
  const dialogId = target.dataset.closeDialog;
  if (dialogId) {
    closeDialog(dialogId);
    return;
  }
  if (target.dataset.openApplicationDialog !== undefined) {
    openDialog("application-dialog");
    window.htmx.ajax("GET", "/applications/new", "#application-dialog-content");
    return;
  }
  if (target.dataset.openStageEditor !== undefined) {
    openDialog("stage-editor-dialog");
    window.htmx.ajax(
      "GET",
      target.dataset.stageEditorUrl,
      "#stage-editor-content",
    );
    return;
  }
  if (target.dataset.openNotesEditor !== undefined) {
    openDialog("notes-editor-dialog");
    window.htmx.ajax(
      "GET",
      target.dataset.notesEditorUrl,
      "#notes-editor-content",
    );
    return;
  }
  if (target.dataset.previewUrl) {
    document.getElementById("cv-preview-frame").src = target.dataset.previewUrl;
    openDialog("cv-preview-dialog");
  }
});
