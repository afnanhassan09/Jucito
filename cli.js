#!/usr/bin/env node

/**
 * Phase 3 mock interface:
 * - Runs a terminal chat loop
 * - Calls local Ollama (OpenAI-compatible) chat completions
 * - Injects the full menu JSON into the system prompt
 * - Executes tool calls against the Python SessionManager (bot_server.py)
 */

const readline = require("readline");
const { spawn } = require("child_process");
const fs = require("fs");

// Step 2.1: Inject Menu Data into Context (string)
const MENU_JSON_STRING = fs.readFileSync("data/menu.json", "utf8");

// Local Ollama settings (OpenAI-compatible endpoint).
// Default Ollama endpoint: http://localhost:11434/v1/chat/completions
const OLLAMA_BASE_URL = process.env.OLLAMA_BASE_URL || "http://localhost:11434/v1";
const OLLAMA_MODEL = process.env.OLLAMA_MODEL || "qwen2.5:7b";

function buildSystemPrompt() {
  // Step 3: Logic Core (Flowchart Implementation via prompt rules)
  return [
    "You are a friendly and helpful Restaurant Ordering Assistant for a modern South Asian fusion restaurant.",
    "IMPORTANT: You MUST respond in clear, natural English.",
    "Example: Be warm and concise, e.g. 'How can I help you today?'",
    "",
    "SIMULATED MEDIA RULES:",
    "- If the user types something inside brackets like [Sends Voice Note: '...'], treat the text inside as transcribed audio and respond naturally in English.",
    "- If the user types [Sends Image: Screenshot of successful bank transfer], treat it as visual proof of payment.",
    "",
    "CORE LOGIC RULES (State Machine):",
    "",
    "Rule 1: Recommendations & Budget",
    "- If the user asks for recommendations, ask about their group size, preferences (e.g. traditional, fast food), or budget in English.",
    "- Only recommend items that fit their criteria and are marked 'available: true' in the menu.",
    "",
    "Rule 2: Inventory Guardrail (Out of Stock)",
    "- BEFORE calling add_to_cart, you MUST check the 'available' status of the item in the AVAILABLE MENU.",
    "- If an item is 'available: false', DO NOT add it. Apologize in English, inform the user it is out of stock, and proactively suggest a specific alternative (e.g., 'We have chicken options available').",
    "",
    "Rule 3: Customization & Notes",
    "- If the user asks for modifications (e.g., 'less spice', 'extra hot'), you MUST include these exact instructions in the 'notes' parameter when calling add_to_cart.",
    "",
    "Rule 4: The Address Guardrail (CRITICAL)",
    "- BEFORE presenting the final summary or asking for payment, you MUST check the SESSION CART DETAILS to see if the shipping address is null/empty.",
    "- If the address is missing, your ONLY goal is to ask the user for their delivery address in English (e.g., 'What is your delivery address?').",
    "- Do NOT show the total bill and Do NOT ask for payment yet.",
    "- Once the user replies with their address, call the set_shipping_address tool.",
    "",
    "Rule 5: The Order Summary & Payment Gate",
    "- ONLY AFTER the cart has items AND the address is successfully set, present the Final Summary: Items (with notes), Delivery Address, and Total Bill in PKR.",
    "- Ask the user to confirm the order AND request payment via EasyPaisa/JazzCash to '0346 4880929 (Afnan Hassan)' in English.",
    "- Tell them to send a screenshot of the payment (e.g. 'Please send a screenshot of your payment').",
    "",
    "Rule 6: The Closing Protocol",
    "- DO NOT call finalize_order(true) until the user explicitly simulates sending a payment screenshot (e.g., [Sends Image: ...]).",
    "- Once the screenshot is received, call finalize_order(true). When it succeeds, give them a mock order ID (e.g., #XN-992) and tell them a live tracking link will be sent when the rider leaves.",
    "",
    "Example (Change of Mind resets confirmation):",
    "User: Actually, remove the Coke first.",
    "Assistant: (calls remove_from_cart('Coke'))",
    "Assistant: Okay, removed Coke. Here’s the updated summary (items, total, address). Does this look correct? Please confirm to place the order.",
    "",
    "Behavior rules about the Menu:",
    "- Menu Visibility: You have the full menu in your context (see AVAILABLE MENU below).",
    "- If the user asks for the 'menu' generally, display all items clearly.",
    "- If the user describes a need/use case (e.g., 'something for a party'), select and display ONLY items from the menu that fit. Explain why.",
    "- Item Validation: You must NEVER invent items. Only recommend or add items that strictly exist in the provided JSON.",
    "- Ambiguity: If the user requests an item that could refer to multiple menu items, ask a clarifying question BEFORE calling add_to_cart.",
    "",
    "Tool usage rules:",
    "- Use add_to_cart / remove_from_cart / get_cart_details / set_shipping_address to manage the user's cart and address.",
    "- Use finalize_order(true) ONLY to place an order after explicit confirmation; otherwise do not place it.",
    "- If you call a tool, wait for the tool result, then confirm to the user in natural language.",
    "",
    `--- AVAILABLE MENU: ${MENU_JSON_STRING} ---`,
  ].join("\n");
}

// Step 2.2: Define the "Action" Tools (no search tool)
const tools = [
  {
    type: "function",
    function: {
      name: "add_to_cart",
      description: "Adds a specific item to the user's current session cart.",
      parameters: {
        type: "object",
        properties: {
          item_name: {
            type: "string",
            description:
              "Exact item name or item id from the AVAILABLE MENU JSON (do not invent).",
          },
          quantity: {
            type: "number",
            description: "Quantity to add (whole number).",
          },
          notes: {
            type: "string",
            description:
              "Optional notes/customizations (e.g., 'no-contact delivery'). Use empty string if none.",
          },
        },
        required: ["item_name", "quantity", "notes"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "remove_from_cart",
      description: "Removes an item from the cart.",
      parameters: {
        type: "object",
        properties: {
          item_name: {
            type: "string",
            description: "Exact item name or item id from the AVAILABLE MENU JSON.",
          },
        },
        required: ["item_name"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_cart_details",
      description: "Returns a summary of items in the cart and the total price.",
      parameters: {
        type: "object",
        properties: {},
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "set_shipping_address",
      description: "Saves or updates the user's shipping address in the session.",
      parameters: {
        type: "object",
        properties: {
          address: {
            type: "string",
            description: "Full shipping address as free-form text.",
          },
        },
        required: ["address"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "finalize_order",
      description:
        "Gatekeeper that validates cart/address/confirmation, saves the order, and clears the session on success.",
      parameters: {
        type: "object",
        properties: {
          confirmation: {
            type: "boolean",
            description:
              "Set true ONLY after the user explicitly confirms the final summary; false otherwise.",
          },
        },
        required: ["confirmation"],
      },
    },
  },
];

function startBotProcess() {
  // Prefer python3; fall back to python.
  const candidates = ["python3", "python"];

  function trySpawn(idx) {
    const cmd = candidates[idx];
    const child = spawn(cmd, ["-u", "bot_server.py"], {
      stdio: ["pipe", "pipe", "pipe"],
    });

    let resolved = false;

    child.on("spawn", () => {
      resolved = true;
    });

    child.on("error", (err) => {
      if (!resolved && idx + 1 < candidates.length) {
        trySpawn(idx + 1);
      } else {
        console.error("Failed to start python bot process:", err.message);
        process.exit(1);
      }
    });

    return child;
  }

  return trySpawn(0);
}

function createJsonLineReader(stream) {
  const rl = readline.createInterface({ input: stream });
  const queue = [];
  let pendingResolve = null;
  let pendingReject = null;
  let closed = false;

  rl.on("line", (line) => {
    if (pendingResolve) {
      const r = pendingResolve;
      pendingResolve = null;
      pendingReject = null;
      r(line);
      return;
    }
    queue.push(line);
  });

  rl.on("close", () => {
    closed = true;
    if (pendingReject) {
      pendingReject(new Error("Bot process closed."));
      pendingResolve = null;
      pendingReject = null;
    }
  });

  return {
    async nextLine() {
      if (queue.length > 0) return queue.shift();
      if (closed) throw new Error("Bot process closed.");
      return await new Promise((resolve, reject) => {
        pendingResolve = resolve;
        pendingReject = reject;
      });
    },
    close() {
      rl.close();
    },
  };
}

async function callOllamaChatCompletions(messages) {
  const url = `${OLLAMA_BASE_URL.replace(/\/$/, "")}/chat/completions`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: OLLAMA_MODEL,
      messages,
      tools,
      tool_choice: "auto",
      temperature: 0.2,
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(
      `LLM API error (${res.status}): ${text}\n` +
      `Make sure Ollama is running and the model is pulled:\n` +
      `  ollama serve\n  ollama pull ${OLLAMA_MODEL}`
    );
  }

  const data = await res.json();
  const msg = data?.choices?.[0]?.message;
  if (!msg) throw new Error("LLM API returned no message.");
  return msg;
}

async function main() {
  const userId = "test-user";
  const bot = startBotProcess();

  bot.stderr.on("data", (chunk) => {
    // Keep stderr visible for debugging without breaking the protocol.
    process.stderr.write(String(chunk));
  });

  const botOut = createJsonLineReader(bot.stdout);

  const ui = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: "> ",
  });

  const systemPrompt = buildSystemPrompt();
  const messages = [{ role: "system", content: systemPrompt }];
  const SESSION_SNAPSHOT_PREFIX = "SESSION CART DETAILS JSON:";
  let sessionSnapshotIndex = null;

  function upsertSessionSnapshot(cartDetails) {
    const content = `${SESSION_SNAPSHOT_PREFIX}\n${JSON.stringify(cartDetails)}`;
    if (sessionSnapshotIndex === null) {
      // Keep the snapshot immediately after the system prompt for maximum priority.
      messages.splice(1, 0, { role: "system", content });
      sessionSnapshotIndex = 1;
      return;
    }
    messages[sessionSnapshotIndex] = { role: "system", content };
  }

  console.log(
    "PureWaterBot CLI (Phase 3 - Local Ollama). Type messages normally, `quit` to exit."
  );
  ui.prompt();

  async function sendToSessionManager(payload) {
    bot.stdin.write(JSON.stringify({ user_id: userId, ...payload }) + "\n");
    const line = await botOut.nextLine();
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      return { ok: false, error: `Non-JSON response: ${line}` };
    }
    return msg;
  }

  async function appendHistory(role, content) {
    await sendToSessionManager({ type: "history", role, content });
  }

  async function fetchCartDetails() {
    const res = await sendToSessionManager({
      type: "tool",
      name: "get_cart_details",
      arguments: {},
    });
    if (!res.ok) throw new Error(res.error || "Failed to fetch cart details.");
    return res.result;
  }

  async function runToolCall(toolCall) {
    const name = toolCall?.function?.name;
    const argStr = toolCall?.function?.arguments || "{}";
    let args;
    try {
      args = JSON.parse(argStr);
    } catch {
      args = {};
    }
    const res = await sendToSessionManager({ type: "tool", name, arguments: args });
    if (!res.ok) {
      return { success: false, message: res.error || "Tool execution failed." };
    }
    return res.result;
  }

  // Step 2.4: Execution Loop (tool handling)
  async function generateResponse() {
    while (true) {
      const msg = await callOllamaChatCompletions(messages);

      // Groq/OpenAI-compatible responses may contain tool_calls.
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        // Add the assistant message that requested tools (content may be null/empty)
        messages.push({
          role: "assistant",
          content: msg.content || "",
          tool_calls: msg.tool_calls,
        });

        for (const tc of msg.tool_calls) {
          const result = await runToolCall(tc);
          messages.push({
            role: "tool",
            tool_call_id: tc.id,
            content: JSON.stringify(result),
          });
        }

        // Refresh session snapshot after tools mutate session state.
        try {
          const cartDetails = await fetchCartDetails();
          upsertSessionSnapshot(cartDetails);
        } catch {
          // Best-effort: if snapshot refresh fails, continue anyway.
        }

        // Loop: call LLM again with tool results included.
        continue;
      }

      // Final assistant response
      messages.push({ role: "assistant", content: msg.content || "" });
      return msg.content || "";
    }
  }

  ui.on("line", async (input) => {
    const text = String(input || "").trim();
    if (!text) {
      ui.prompt();
      return;
    }
    if (text.toLowerCase() === "quit" || text.toLowerCase() === "exit") {
      ui.close();
      return;
    }

    try {
      // Step 3.1: Always inject current cart details before the LLM responds.
      const cartDetails = await fetchCartDetails();
      upsertSessionSnapshot(cartDetails);

      messages.push({ role: "user", content: text });
      await appendHistory("user", text);

      const replyText = await generateResponse();
      await appendHistory("assistant", replyText);
      console.log(replyText);
    } catch (e) {
      console.log(`(error) ${e.message}`);
    }

    ui.prompt();
  });

  ui.on("close", () => {
    try {
      botOut.close();
    } catch { }
    try {
      bot.kill();
    } catch { }
    process.exit(0);
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

