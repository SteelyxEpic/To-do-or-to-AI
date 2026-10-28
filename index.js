/**
 * Slack to-do bot that uses an LLM (via Groq) to turn free text into tasks.
 *
 * Tasks are stored per user in data/<slack_user_id>.json as a tree:
 *   todolist -> category -> subcategory -> ... -> { task title: task entry }
 * A task entry is any object containing the key "Todo".
 */

require("dotenv").config();
const fs = require("fs");
const Groq = require("groq-sdk");
const { App } = require("@slack/bolt");

const SLACK_API_KEY = process.env.SLACK_API_KEY; // Bot token (xoxb-...)

const DATA_DIR = "data";
const LLM_MODEL = "openai/gpt-oss-20b";
const EXPIRY_CHECK_INTERVAL_MS = 10 * 60 * 1000; // every 10 minutes
const MAX_BUTTONS = 45; // Slack allows at most 50 blocks per message
const TASK_FIELDS = ["Todo", "Title", "DueDate", "Time", "Sort"];

const HELP_TEXT = `Available Commands:
/tyo-add <task> - Add a task (e.g. /tyo-add finish report by friday 5pm)
/tyo-get - Browse your tasks
/tyo-categories - See all categories
/tyo-expired - See expired tasks
/tyo-get-done - See done tasks
/tyo-delete - Delete tasks
/tyo-help - Show available commands
`;

const groq = new Groq({ apiKey: process.env.GROQ_API_KEY });

const app = new App({
  token: SLACK_API_KEY,
  appToken: process.env.SLACK_APP_TOKEN,
  socketMode: true
});


// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

function loadUser(userId) {
  const file = `${DATA_DIR}/${userId}.json`;
  if (fs.existsSync(file)) {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  }
  return { todolist: {}, all_keys: [], expired: {}, done: {} };
}

function saveUser(userId, data) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(`${DATA_DIR}/${userId}.json`, JSON.stringify(data, null, 4));
}

function getAllUserIds() {
  if (!fs.existsSync(DATA_DIR)) return [];
  return fs
    .readdirSync(DATA_DIR)
    .filter((name) => name.endsWith(".json"))
    .map((name) => name.slice(0, -".json".length));
}


// ---------------------------------------------------------------------------
// Task tree helpers
// ---------------------------------------------------------------------------

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Walk "a/b/c" down the tree. Missing parts yield an empty object. */
function getNode(tree, path) {
  let node = tree;
  for (const part of path.split("/")) {
    node = (node && node[part]) || {};
  }
  return node;
}

/** Like getNode, but creates any missing categories along the way. */
function getOrCreateNode(tree, path) {
  let node = tree;
  for (const part of path.split("/")) {
    node = node[part] ??= {};
  }
  return node;
}

/** "a/b/title" -> ["a/b", "title"]; "title" -> ["", "title"]. */
function splitPath(path) {
  const index = path.lastIndexOf("/");
  return index === -1 ? ["", path] : [path.slice(0, index), path.slice(index + 1)];
}

/** Remove and return the task at `path` (or null if it isn't there). */
function popTask(tree, path) {
  const [parentPath, title] = splitPath(path);
  const parent = parentPath ? getNode(tree, parentPath) : tree;
  const task = parent[title];
  delete parent[title];
  return task ?? null;
}

/** Return [[path, task], ...] for every task in the tree. */
function findTasks(tree, location = "") {
  const tasks = [];
  for (const [key, value] of Object.entries(tree)) {
    if (!isObject(value)) continue;
    const path = location ? `${location}/${key}` : key;
    if ("Todo" in value) {
      tasks.push([path, value]);
    } else {
      tasks.push(...findTasks(value, path));
    }
  }
  return tasks;
}

/** Hours until the task is due (Infinity if the due date is TBD or unreadable). */
function hoursRemaining(task) {
  if (task.DueDate === "TBD") return Infinity;

  const due = new Date(`${task.DueDate}T${task.Time}`);
  if (Number.isNaN(due.getTime())) return Infinity;

  return (due - Date.now()) / 3_600_000;
}

function formatTask(title, task) {
  return (
    `*Selected task:*\n${title}: ${task.Todo}\n` +
    `Due Date: ${task.DueDate} Time: ${task.Time}\n` +
    `Hours remaining: ${hoursRemaining(task).toFixed(1)}`
  );
}

function formatTaskArchive(heading, tasks) {
  const lines = [heading];
  for (const [path, task] of Object.entries(tasks)) {
    lines.push(`Task: ${path}\nDetails: ${task.Todo}\n`);
  }
  return lines.join("\n");
}


// ---------------------------------------------------------------------------
// LLM: turn free text into a task
// ---------------------------------------------------------------------------

function buildSystemPrompt(knownCategories) {
  const now = new Date();
  const today = now.toLocaleDateString("en-CA"); // YYYY-MM-DD
  const time = now.toTimeString().slice(0, 5); // HH:MM

  return (
    `You are a todo list sorter. Today's date is ${today} and current time is ${time}.` +
    " Extract the task details from the user's text and output a VALID JSON object " +
    "matching this schema exactly:\n" +
    '{"Todo": "string", "Title": "string", "DueDate": "YYYY-MM-DD", ' +
    '"Time": "HH:MM", "Sort": "category/subcategory"}\n' +
    "The 'Sort' field should be a string that represents the category and subcategory " +
    "of the task, separated by a forward slash. If there is no subcategory, just provide " +
    "the category. It can also have multiple subcategories, separated by forward slashes. " +
    `Currently, the categories are: ${JSON.stringify(knownCategories)}. ` +
    "You may create new categories but only if you think it's necessary.\n" +
    "If you think the description does not have enough information to determine the time, " +
    "assume it as '00:00'. If you think the description does not give a due date, " +
    "please write 'TBD'." +
    " Do not return any pleasantries, introduction, or conversational filler. " +
    "Return ONLY the raw JSON object."
  );
}

/** Ask the LLM to parse `text` into { Todo, Title, DueDate, Time, Sort }. */
async function extractTask(text, knownCategories) {
  const response = await groq.chat.completions.create({
    model: LLM_MODEL,
    messages: [
      { role: "system", content: buildSystemPrompt(knownCategories) },
      { role: "user", content: `Extract task details from: '${text}'` }
    ],
    response_format: { type: "json_object" } // forces raw JSON output
  });

  const parsed = JSON.parse(response.choices[0].message.content);
  for (const field of TASK_FIELDS) {
    if (typeof parsed[field] !== "string") {
      throw new Error(`the AI response was missing "${field}"`);
    }
  }
  return parsed;
}

/** Parse `text`, store the task in the user's todo list, and return it. */
async function addTask(text, userId) {
  const user = loadUser(userId);
  const taskData = await extractTask(text, user.all_keys);

  if (!user.all_keys.includes(taskData.Sort)) {
    user.all_keys.push(taskData.Sort);
  }

  const category = getOrCreateNode(user.todolist, taskData.Sort);
  category[taskData.Title] = {
    Todo: taskData.Todo,
    DueDate: taskData.DueDate,
    Time: taskData.Time,
    done: false,
    expired: false
  };

  saveUser(userId, user);
  return taskData;
}


// ---------------------------------------------------------------------------
// Message / button building (Slack Block Kit)
// ---------------------------------------------------------------------------

/** One button, in its own "actions" block so it sits on its own row. */
function button(label, actionId, value = "-") {
  return {
    type: "actions",
    elements: [
      {
        type: "button",
        text: { type: "plain_text", text: label.slice(0, 75) },
        action_id: actionId,
        value
      }
    ]
  };
}

/** Send (or replace) a message made of some text plus button rows. */
async function show(respond, text, buttons = []) {
  const blocks = [
    { type: "section", text: { type: "mrkdwn", text } },
    ...buttons.slice(0, MAX_BUTTONS)
  ];
  await respond({ text, blocks, replace_original: true });
}

/** The "Return" and "Get upcoming task" rows shown on most menus. */
function navigationButtons() {
  return [
    button("Return", "return"),
    button("Get upcoming task", "upcoming")
  ];
}

/** Buttons for the categories/tasks directly under `path` ("" = top level). */
function categoryButtons(path, userId) {
  const todolist = loadUser(userId).todolist;
  const node = path ? getNode(todolist, path) : todolist;

  const buttons = [];
  for (const [name, child] of Object.entries(node)) {
    if (child.done || child.expired) continue;
    const childPath = path ? `${path}/${name}` : name;
    buttons.push(button(name, `get_task:${childPath}`, childPath));
  }
  return buttons;
}

function mainMenuButtons(userId) {
  return [...navigationButtons(), ...categoryButtons("", userId)];
}


// ---------------------------------------------------------------------------
// Slash commands
// ---------------------------------------------------------------------------

app.command("/tyo-help", async ({ ack, respond }) => {
  await ack();
  await respond({ text: HELP_TEXT });
});

app.command("/tyo-add", async ({ command, ack, respond }) => {
  await ack(); // Slack requires an acknowledgement within 3 seconds

  const text = command.text.trim();
  if (!text) {
    await respond({ text: "Please describe your task, e.g. `/tyo-add finish report by friday 5pm`" });
    return;
  }

  try {
    const taskData = await addTask(text, command.user_id);
    await respond({ text: `Done! Added task with title '${taskData.Title}'` });
  } catch (err) {
    await respond({ text: `Sorry, I couldn't add that task: ${err.message}` });
  }
});

app.command("/tyo-categories", async ({ command, ack, respond }) => {
  await ack();
  const user = loadUser(command.user_id);
  await respond({ text: `Available categories: ${JSON.stringify(user.all_keys)}` });
});

app.command("/tyo-get", async ({ command, ack, respond }) => {
  await ack();
  await show(respond, "Choose an option:", mainMenuButtons(command.user_id));
});

app.command("/tyo-get-done", async ({ command, ack, respond }) => {
  await ack();
  const done = loadUser(command.user_id).done ?? {};
  await respond({
    text: Object.keys(done).length ? formatTaskArchive("*Done tasks:*", done) : "No done tasks."
  });
});

app.command("/tyo-expired", async ({ command, ack, respond }) => {
  await ack();
  const expired = loadUser(command.user_id).expired ?? {};
  await respond({
    text: Object.keys(expired).length ? formatTaskArchive("*Expired tasks:*", expired) : "No expired tasks."
  });
});

app.command("/tyo-delete", async ({ command, ack, respond }) => {
  await ack();

  const tasks = findTasks(loadUser(command.user_id).todolist);
  if (tasks.length === 0) {
    await respond({ text: "No tasks available to delete." });
    return;
  }

  const buttons = tasks.map(([path, task]) =>
    button(`${splitPath(path)[1]}: ${task.Todo}`, `delete:${path}`, path)
  );
  buttons.push(button("Delete All", "delete_all"));
  await show(respond, "Select a task to delete:", buttons);
});


// ---------------------------------------------------------------------------
// Button click handlers
// ---------------------------------------------------------------------------

app.action("return", async ({ ack, body, respond }) => {
  await ack();
  await show(respond, "Choose an option:", mainMenuButtons(body.user.id));
});

app.action("upcoming", async ({ ack, body, respond }) => {
  await ack();
  const user = loadUser(body.user.id);

  // Only consider tasks that haven't passed yet.
  const upcoming = findTasks(user.todolist).filter(([, task]) => hoursRemaining(task) >= 0);
  if (upcoming.length === 0) {
    await show(respond, "No tasks available.", navigationButtons());
    return;
  }

  const [path, task] = upcoming.reduce((best, current) =>
    hoursRemaining(current[1]) < hoursRemaining(best[1]) ? current : best
  );
  const buttons = [...navigationButtons(), button("Mark as done", `done_task:${path}`, path)];
  await show(respond, formatTask(splitPath(path)[1], task), buttons);
});

app.action(/^get_task:/, async ({ ack, body, action, respond }) => {
  await ack();
  const userId = body.user.id;
  const path = action.value;
  const node = getNode(loadUser(userId).todolist, path);

  const buttons = navigationButtons();
  let text;
  if ("Todo" in node) { // it's a task
    text = formatTask(splitPath(path)[1], node);
    buttons.push(button("Mark as done", `done_task:${path}`, path));
  } else { // it's a category
    text = `*Selected category:* ${path}`;
    buttons.push(...categoryButtons(path, userId));
  }

  await show(respond, text, buttons);
});

app.action(/^done_task:/, async ({ ack, body, action, respond }) => {
  await ack();
  const userId = body.user.id;
  const path = action.value;

  const user = loadUser(userId);
  const task = popTask(user.todolist, path);
  const title = splitPath(path)[1];

  let text;
  if (task === null) {
    text = `Task not found: ${title}`;
  } else {
    user.done[path] = task;
    saveUser(userId, user);
    text = `Task marked as done: ${title}`;
  }

  await show(respond, text, navigationButtons());
});

app.action(/^delete:/, async ({ ack, body, action, respond }) => {
  await ack();
  const userId = body.user.id;
  const path = action.value;

  const user = loadUser(userId);
  const removed = popTask(user.todolist, path);
  saveUser(userId, user);

  const title = splitPath(path)[1];
  await show(respond, removed ? `Task '${title}' has been deleted.` : `Task '${title}' not found.`);
});

app.action("delete_all", async ({ ack, body, respond }) => {
  await ack();
  const userId = body.user.id;

  const user = loadUser(userId);
  user.todolist = {};
  saveUser(userId, user);

  await show(respond, "All tasks have been deleted.");
});


// ---------------------------------------------------------------------------
// Background job: move overdue tasks to "expired" and notify the user
// ---------------------------------------------------------------------------

async function checkExpiredTasks() {
  for (const userId of getAllUserIds()) {
    const user = loadUser(userId);
    let changed = false;

    for (const [path, task] of findTasks(user.todolist)) {
      if (hoursRemaining(task) >= 0) continue;

      task.expired = true;
      user.expired[path] = popTask(user.todolist, path);
      changed = true;

      // Posting to a user ID sends a DM from the bot.
      await app.client.chat.postMessage({
        token: SLACK_API_KEY,
        channel: userId,
        text: `The task '${splitPath(path)[1]}' has expired!`
      });
    }

    if (changed) saveUser(userId, user);
  }
}

async function runExpiryCheck() {
  try {
    await checkExpiredTasks();
  } catch (err) { // keep the timer alive no matter what
    console.error("Expiry check failed:", err.message);
  }
}


// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

(async () => {
  await app.start();
  setTimeout(runExpiryCheck, 10_000);
  setInterval(runExpiryCheck, EXPIRY_CHECK_INTERVAL_MS);
  console.log("bot is running!");
})();