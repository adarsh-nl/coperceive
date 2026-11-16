// Fails the capture when a screen's page text contains an identifier.
// The app's own metric label "mAP@" is the one "@" the viewer always shows.
const fs = require("fs");
const path = require("path");

function patternFileNeedles() {
  const file = path.resolve(__dirname, "../../ci/identifier_patterns.txt");
  if (!fs.existsSync(file)) return [];
  return fs
    .readFileSync(file, "utf8")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => line.replace(/\\\./g, ".").replace(/\+$/g, ""));
}

function needles() {
  const fromEnv = [process.env.TOUR_HOSTNAME, process.env.TOUR_USER, process.env.TOUR_HOME]
    .map((value) => (value || "").trim())
    .filter((value) => value.length >= 3);
  return ["$USER", "$HOME", "tudelft", "utwente", ...patternFileNeedles(), ...fromEnv];
}

function hitsIn(text) {
  const hits = [];
  for (const needle of needles()) {
    if (text.includes(needle)) hits.push(needle);
  }
  // The app's own labels. Anything else with "@" is treated as an identifier.
  const withoutLabels = text.replace(/mAP@/g, "mAP").replace(/\d+ @ \d+ Hz/g, "rate");
  if (withoutLabels.includes("@")) hits.push("@");
  return hits;
}

function checkText(text, label) {
  const log = process.env.TOUR_TEXT_LOG;
  if (log) fs.appendFileSync(log, `\n--- ${label}\n${text}\n`);
  const hits = hitsIn(text);
  if (hits.length) {
    console.error(`forbidden page text in ${label}: ${hits.join(", ")}`);
    process.exit(1);
  }
}

function checkPublished(text, label) {
  checkText(text, label);
  if (text.includes("only available in the desktop app")) {
    console.error(`desktop-only line in ${label}`);
    process.exit(1);
  }
}

function noteLoop(text, label) {
  if (/Buffering|Failed to fetch|disconnected/i.test(text)) {
    console.error(`bad loop frame ${label}`);
    process.exit(1);
  }
}

module.exports = { checkText, checkPublished, noteLoop };
