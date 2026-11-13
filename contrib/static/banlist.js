// Builds setban commands from the entities the reader picked. The entity data
// is embedded in the page as JSON, so this works offline and without a fetch.

(function () {
  "use strict";

  const data = JSON.parse(document.getElementById("entity-data").textContent);
  const entities = {};
  data.entities.forEach(function (entity) {
    entities[entity.slug] = entity;
  });

  const entityBoxes = Array.from(document.querySelectorAll(".entity-toggle"));
  const tagBoxes = Array.from(document.querySelectorAll(".tag-toggle"));
  const output = document.getElementById("output");
  const format = document.getElementById("format");
  const days = document.getElementById("days");
  const count = document.getElementById("count");
  const copyButton = document.getElementById("copy");
  const copiedNote = document.getElementById("copied");
  const clearButton = document.getElementById("clear");

  const CLI_NOTE =
    "# setban commands built from a selection on the Bitcoin network entities site.\n" +
    "# This is not a script. Read every line before running it.";

  function tagsOf(box) {
    const tags = box.getAttribute("data-tags");
    return tags ? tags.split(" ") : [];
  }

  function boxesWithTag(tag) {
    return entityBoxes.filter(function (box) {
      return tagsOf(box).indexOf(tag) !== -1;
    });
  }

  function selection() {
    return entityBoxes
      .filter(function (box) {
        return box.checked;
      })
      .map(function (box) {
        return entities[box.value];
      })
      .filter(Boolean);
  }

  function plural(n, singular, pluralForm) {
    return n + " " + (n === 1 ? singular : pluralForm);
  }

  function banSeconds() {
    const value = parseInt(days.value, 10);
    return (Number.isFinite(value) && value > 0 ? value : 1) * 86400;
  }

  // A tag is checked when all of its entities are, and indeterminate when only
  // some of them are.
  function syncTagBoxes() {
    tagBoxes.forEach(function (tagBox) {
      const boxes = boxesWithTag(tagBox.value);
      const checked = boxes.filter(function (box) {
        return box.checked;
      }).length;
      tagBox.checked = boxes.length > 0 && checked === boxes.length;
      tagBox.indeterminate = checked > 0 && checked < boxes.length;
    });
  }

  function render() {
    const selected = selection();
    let networks = 0;
    selected.forEach(function (entity) {
      networks += entity.networks.length;
    });

    count.textContent = selected.length
      ? plural(selected.length, "entity", "entities") +
        " with " +
        plural(networks, "network", "networks") +
        " selected."
      : "Nothing selected.";

    const plain = format.value === "plain";
    days.disabled = plain;
    copyButton.disabled = selected.length === 0;
    copiedNote.hidden = true;

    if (!selected.length) {
      output.textContent = "Select one or more entities above.";
      return;
    }

    let width = 0;
    selected.forEach(function (entity) {
      entity.networks.forEach(function (network) {
        width = Math.max(width, network.length);
      });
    });

    const seconds = banSeconds();
    const lines = format.value === "cli" ? [CLI_NOTE] : [];
    selected.forEach(function (entity) {
      entity.networks.forEach(function (network) {
        if (plain) {
          lines.push(network);
        } else if (format.value === "gui") {
          lines.push("setban " + network.padEnd(width) + " add " + seconds);
        } else {
          lines.push(
            "bitcoin-cli setban " + network.padEnd(width) + " add " + seconds + "  # " + entity.name
          );
        }
      });
    });
    output.textContent = lines.join("\n");
  }

  entityBoxes.forEach(function (box) {
    box.addEventListener("change", function () {
      syncTagBoxes();
      render();
    });
  });

  tagBoxes.forEach(function (tagBox) {
    tagBox.addEventListener("change", function () {
      boxesWithTag(tagBox.value).forEach(function (box) {
        box.checked = tagBox.checked;
      });
      syncTagBoxes();
      render();
    });
  });

  format.addEventListener("change", render);
  days.addEventListener("input", render);

  clearButton.addEventListener("click", function () {
    entityBoxes.forEach(function (box) {
      box.checked = false;
    });
    syncTagBoxes();
    render();
  });

  copyButton.addEventListener("click", function () {
    const text = output.textContent;
    const done = function () {
      copiedNote.hidden = false;
      window.setTimeout(function () {
        copiedNote.hidden = true;
      }, 2000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallbackCopy);
    } else {
      fallbackCopy();
    }

    function fallbackCopy() {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        done();
      } catch (e) {
        /* The reader can still select the text by hand. */
      }
      document.body.removeChild(area);
    }
  });

  // The browser may have restored checkbox states on reload.
  syncTagBoxes();
  render();
})();
