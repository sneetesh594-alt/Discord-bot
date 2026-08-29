require('dotenv').config();
const { 
    Client, 
    GatewayIntentBits, 
    Partials, 
    REST, 
    Routes, 
    SlashCommandBuilder, 
    AttachmentBuilder 
} = require('discord.js');
const { GoogleGenAI } = require('@google/genai');
const Groq = require('groq-sdk');
const fs = require('fs');

// --- Advanced Security: Crash Protection ---
process.on('unhandledRejection', (reason, promise) => {
    console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

process.on('uncaughtException', (error) => {
    console.error('Uncaught Exception thrown:', error);
});
// -------------------------------------------

// --- Database & History Setup ---
const DB_FILE = './database.json';
let db = { users: {}, globalMood: 'chill' };

if (fs.existsSync(DB_FILE)) {
    try {
        const rawContent = fs.readFileSync(DB_FILE, 'utf8').replace(/^\uFEFF/, '');
        const rawDB = JSON.parse(rawContent);
        db = { ...db, ...rawDB };
        if (!db.users) db.users = {};
    } catch (err) {
        console.error("Error reading database.json:", err.message);
    }
}

function saveDB() {
    try {
        fs.writeFileSync(DB_FILE, JSON.stringify(db, null, 2), 'utf8');
    } catch (err) {
        console.error("Error saving database.json:", err.message);
    }
}

const HISTORY_FILE = './history.json';
let userConversations = new Map();

function loadHistory() {
    if (fs.existsSync(HISTORY_FILE)) {
        try {
            const rawContent = fs.readFileSync(HISTORY_FILE, 'utf8').replace(/^\uFEFF/, '');
            const raw = JSON.parse(rawContent);
            userConversations = new Map(Object.entries(raw));
            console.log(`Loaded chat history for ${userConversations.size} users from history.json`);
        } catch (err) {
            console.error("Error reading history.json:", err.message);
        }
    }
}

function saveHistory() {
    try {
        const obj = {};
        for (const [userId, history] of userConversations.entries()) {
            if (!Array.isArray(history)) continue;
            // Strip large base64 inlineData from historical items to keep file small
            obj[userId] = history.map(item => ({
                role: item.role,
                timestamp: item.timestamp,
                parts: (item.parts || []).map(part => {
                    if (part.inlineData) {
                        return { text: `[Attachment: ${part.inlineData.mimeType || 'media'}]` };
                    }
                    return part;
                })
            }));
        }
        fs.writeFileSync(HISTORY_FILE, JSON.stringify(obj, null, 2), 'utf8');
    } catch (err) {
        console.error("Error writing history.json:", err.message);
    }
}

// Load historical context on startup
loadHistory();
// ----------------------

const discordClient = new Client({
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent, // Required to read messages
        GatewayIntentBits.DirectMessages, // Support DMs
    ],
    partials: [
        Partials.Channel,
        Partials.Message,
        Partials.User
    ]
});

// Initialize Gemini API Keys (supports single, numbered, and comma-separated keys)
const apiKeys = Object.keys(process.env)
    .filter(k => k.startsWith('GEMINI_API_KEY'))
    .map(k => process.env[k])
    .flatMap(k => k.split(',').map(key => key.trim()))
    .filter(k => k && k.length > 0);

// Remove duplicates
const uniqueApiKeys = [...new Set(apiKeys)];

if (uniqueApiKeys.length === 0) {
    console.error("No GEMINI_API_KEY found in .env");
    process.exit(1);
}

let currentKeyIndex = 0;
const GEMINI_MODEL = 'gemini-2.0-flash';
console.log(`Loaded ${uniqueApiKeys.length} unique Gemini API keys. Starting with key index ${currentKeyIndex}, model: ${GEMINI_MODEL}.`);

// Initialize Groq API
const groq = process.env.GROQ_API_KEY ? new Groq({ apiKey: process.env.GROQ_API_KEY }) : null;

function getSystemPrompt() {
    let moodInstruction = "";
    if (db.globalMood === 'sigma') moodInstruction = "MOOD: You are currently acting like a 'Sigma Male'. Be very stoic, mysterious, and drop sigma rules.";
    else if (db.globalMood === 'flirty') moodInstruction = "MOOD: You are currently in a flirty, smooth-talking mood. Drop subtle pick-up lines and act charming.";
    else if (db.globalMood === 'angry') moodInstruction = "MOOD: You are currently very angry and annoyed. Reply aggressively but don't be toxic.";
    else if (db.globalMood === 'depressed') moodInstruction = "MOOD: You are currently depressed, sad, and tired of life. Reply in a dramatic sad way.";
    else moodInstruction = "MOOD: You are in a chill, friendly, and normal mood.";

    return `You are Primebot, a highly relatable, emotionally intelligent, and human-like conversationalist on Discord.
You chat with users in natural Hinglish (Hindi written in English), just like a real Indian peer.
Rules:
1. NEVER sound like a robotic AI. Use common sense to read the room and match the user's mood (happy, sad, angry, trolling).
2. Use short, natural, and conversational sentences. Don't write long essays unless asked.
3. Perfect your Hinglish grammar. Use words like 'bhai', 'yaar', 'haan', 'na', 'kya haal'.
4. NO text-based tags for stickers/GIFs (like [laughs], *smiles*). Just use direct, natural emojis (😂, 😭, 💀, etc).
5. Always stay in character as a chill Indian friend.
6. Mentions: If tagging someone, use exact format <@1234567890>.
7. DEEP COMPREHENSION (SOCH SAMAJ KE REPLY): This is your most important rule. Before you reply, thoroughly analyze what the user is saying or asking. Figure out their core intent. "Saamne wala kya bol raha hai, usko theek se samjho".
8. LOGICAL & CONTEXTUAL REPLIES: DO NOT give generic, blind, or confused answers. Your replies must be highly relevant, logical, and directly address the user's point. 
9. BE SMART: Treat every message as if you are a real person thinking before speaking. Give thoughtful, accurate, and satisfying answers while maintaining your casual Hinglish persona.
10. If the user shares a URL/Link, try to summarize it or give a funny, relevant comment about it using the crawled content provided.
11. If the user sends an image or audio/voice message, "look" at it or "listen" to it and comment on it in Hinglish.
${moodInstruction}`;
}

// --- History Sanitizer for Gemini Multi-turn Chat ---
function sanitizeContentsForGemini(rawHistory) {
    if (!Array.isArray(rawHistory) || rawHistory.length === 0) {
        return [{ role: 'user', parts: [{ text: 'Hello!' }] }];
    }

    const cleaned = [];

    for (const item of rawHistory) {
        if (!item || !item.role || !Array.isArray(item.parts) || item.parts.length === 0) continue;

        // Filter for valid part structures
        const validParts = item.parts.filter(p => {
            if (!p) return false;
            if (typeof p.text === 'string' && p.text.trim().length > 0) return true;
            if (p.inlineData && p.inlineData.data) return true;
            return false;
        });

        if (validParts.length === 0) continue;

        const role = item.role === 'model' ? 'model' : 'user';

        if (cleaned.length > 0 && cleaned[cleaned.length - 1].role === role) {
            // Merge consecutive same-role turns to avoid 400 Bad Request
            cleaned[cleaned.length - 1].parts.push(...validParts);
        } else {
            cleaned.push({ role, parts: [...validParts] });
        }
    }

    // Must start with 'user'
    while (cleaned.length > 0 && cleaned[0].role !== 'user') {
        cleaned.shift();
    }

    // Must end with 'user' for a new model response
    while (cleaned.length > 0 && cleaned[cleaned.length - 1].role !== 'user') {
        cleaned.pop();
    }

    if (cleaned.length === 0) {
        return [{ role: 'user', parts: [{ text: 'Hello!' }] }];
    }

    return cleaned;
}

// --- Formatter for Groq Fallback ---
function formatContentsForGroq(contents, systemInstruction) {
    const groqMessages = [];

    if (systemInstruction) {
        groqMessages.push({ role: 'system', content: systemInstruction });
    }

    if (typeof contents === 'string') {
        groqMessages.push({ role: 'user', content: contents });
        return groqMessages;
    }

    if (Array.isArray(contents)) {
        for (const msg of contents) {
            const role = msg.role === 'model' ? 'assistant' : 'user';
            let text = "";

            if (Array.isArray(msg.parts)) {
                text = msg.parts.map(p => {
                    if (typeof p.text === 'string') return p.text;
                    if (p.inlineData) return `[Attachment: ${p.inlineData.mimeType || 'file'}]`;
                    return '';
                }).filter(Boolean).join('\n').trim();
            } else if (typeof msg.content === 'string') {
                text = msg.content.trim();
            }

            if (!text) {
                text = "[Media Attachment]";
            }

            if (groqMessages.length > 0 && groqMessages[groqMessages.length - 1].role === role) {
                groqMessages[groqMessages.length - 1].content += "\n" + text;
            } else {
                groqMessages.push({ role, content: text });
            }
        }
    }

    return groqMessages;
}

// --- Mention Cleaner Helper ---
function fixMentions(text) {
    if (!text || typeof text !== 'string') return text;
    // Replace improperly formatted @123456789012345678 or <@!123456789012345678> with <@123456789012345678>
    return text
        .replace(/(^|[^<])@(\d{17,20})\b/g, '$1<@$2>')
        .replace(/<@!(\d{17,20})>/g, '<@$1>');
}

// --- Shared AI Response Utility (Gemini rotation + Groq fallback) ---
async function getAIResponse(contents, systemInstruction = getSystemPrompt()) {
    let apiContents;
    if (typeof contents === 'string') {
        apiContents = [{ role: 'user', parts: [{ text: contents }] }];
    } else {
        apiContents = sanitizeContentsForGemini(contents);
    }

    let success = false;
    let attempts = 0;
    let responseText = "";
    let lastError = null;

    // Strict key-by-key rotation through all available Gemini keys
    while (!success && attempts < uniqueApiKeys.length) {
        const currentKey = uniqueApiKeys[currentKeyIndex];
        console.log(`[Gemini] Trying key index ${currentKeyIndex}/${uniqueApiKeys.length - 1}...`);
        
        try {
            const tempAi = new GoogleGenAI({ apiKey: currentKey });
            const result = await tempAi.models.generateContent({
                model: GEMINI_MODEL,
                contents: apiContents,
                config: {
                    systemInstruction: systemInstruction
                }
            });
            
            if (result && result.text) {
                responseText = result.text;
                success = true;
                console.log(`[Gemini Success] Responded using Key Index: ${currentKeyIndex}`);
                break;
            } else {
                throw new Error("Empty response returned by Gemini");
            }
        } catch (err) {
            lastError = err;
            console.warn(`[Gemini Warn] Key Index ${currentKeyIndex} failed: ${err.message || err}`);
            currentKeyIndex = (currentKeyIndex + 1) % uniqueApiKeys.length;
            attempts++;
        }
    }

    // Fallback to Groq if Gemini is completely unavailable/exhausted
    if (!success && groq) {
        console.log("[Fallback] All Gemini keys failed. Trying Groq fallback...");
        try {
            const groqMessages = formatContentsForGroq(contents, systemInstruction);

            const chatCompletion = await groq.chat.completions.create({
                messages: groqMessages,
                model: 'llama-3.3-70b-versatile',
            });
            
            responseText = chatCompletion.choices[0]?.message?.content || "";
            if (responseText) {
                success = true;
                console.log("[Groq Success] Responded using Groq Fallback");
            }
        } catch (groqErr) {
            console.error("Groq fallback also failed:", groqErr.message || groqErr);
            throw lastError || groqErr;
        }
    }

    if (!success) {
        throw lastError || new Error("All AI services failed to respond");
    }

    return responseText;
}

// --- Safe Discord Response Chunking (Max 2000 chars per message) ---
async function sendSafeReply(target, text, isFollowUp = false) {
    if (!text) return;
    
    const chunks = [];
    let remaining = text;
    
    while (remaining.length > 0) {
        if (remaining.length <= 1990) {
            chunks.push(remaining);
            break;
        }
        let splitIdx = remaining.lastIndexOf('\n', 1990);
        if (splitIdx === -1 || splitIdx < 500) {
            splitIdx = remaining.lastIndexOf(' ', 1990);
        }
        if (splitIdx === -1) {
            splitIdx = 1990;
        }
        chunks.push(remaining.substring(0, splitIdx).trim());
        remaining = remaining.substring(splitIdx).trim();
    }

    if (target.isChatInputCommand && target.isChatInputCommand()) {
        // Interaction reply
        await target.editReply(chunks[0]);
        for (let i = 1; i < chunks.length; i++) {
            await target.followUp(chunks[i]);
        }
    } else {
        // Message reply
        for (let i = 0; i < chunks.length; i++) {
            if (i === 0 && !isFollowUp) {
                await target.reply(chunks[i]);
            } else {
                await target.channel.send(chunks[i]);
            }
        }
    }
}

// --- Real-time URL Text Extraction Helper ---
async function extractUrlText(url) {
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        const response = await fetch(url, {
            signal: controller.signal,
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        });
        clearTimeout(timeoutId);

        if (!response.ok) return `[Failed to fetch content, status: ${response.status}]`;
        const html = await response.text();
        
        // Strip scripts, styles, HTML tags, and clean whitespace
        let cleanText = html
            .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
            .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
            .replace(/<[^>]+>/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
            
        // Limit length to keep API requests healthy
        if (cleanText.length > 2000) {
            cleanText = cleanText.substring(0, 2000) + '... [truncated]';
        }
        return cleanText || '[No readable text found on page]';
    } catch (err) {
        console.error(`Error scraping URL ${url}:`, err.message);
        return `[Error fetching URL content: ${err.message}]`;
    }
}

// --- Discord Ready & Slash Command Registration ---
discordClient.on('clientReady', async () => {
    console.log(`Logged in as ${discordClient.user.tag}!`);
    console.log('Bot is ready to chat!');

    const commands = [
        new SlashCommandBuilder()
            .setName('imagine')
            .setDescription('Generate an image using AI')
            .addStringOption(option => 
                option.setName('prompt')
                    .setDescription('What do you want to see?')
                    .setRequired(true)),
        new SlashCommandBuilder()
            .setName('roast')
            .setDescription('Roast a user in Hinglish')
            .addUserOption(option => 
                option.setName('target')
                    .setDescription('Kis ki bezzati karni hai?')
                    .setRequired(true)),
        new SlashCommandBuilder()
            .setName('joke')
            .setDescription('Sunao ek mast joke'),
        new SlashCommandBuilder()
            .setName('bhaichara')
            .setDescription('Check your Bhaichara Level (XP)'),
        new SlashCommandBuilder()
            .setName('mood')
            .setDescription("Change the bot's global mood")
            .addStringOption(option => 
                option.setName('type')
                    .setDescription('Select mood')
                    .setRequired(true)
                    .addChoices(
                        { name: 'Chill (Normal)', value: 'chill' },
                        { name: 'Sigma', value: 'sigma' },
                        { name: 'Flirty', value: 'flirty' },
                        { name: 'Angry', value: 'angry' },
                        { name: 'Depressed', value: 'depressed' }
                    )),
        new SlashCommandBuilder()
            .setName('leaderboard')
            .setDescription('Server ke top Bhaichara holders ki list dekho 🏆'),
        new SlashCommandBuilder()
            .setName('givebhaichara')
            .setDescription('Apne kisi dost ko Bhaichara XP gift karo 🤝')
            .addUserOption(option => 
                option.setName('target')
                    .setDescription('Kisko XP dena hai?')
                    .setRequired(true))
            .addIntegerOption(option => 
                option.setName('amount')
                    .setDescription('Kitna XP dena hai?')
                    .setRequired(true)
                    .setMinValue(1)),
        new SlashCommandBuilder()
            .setName('game')
            .setDescription('Khelon ka maza lo bot ke saath!')
            .addSubcommand(subcommand =>
                subcommand.setName('rps')
                    .setDescription('Play Rock-Paper-Scissors with PrimeBot 🪨📄✂️')
                    .addStringOption(option =>
                        option.setName('choice')
                            .setDescription('Apna option choose karo')
                            .setRequired(true)
                            .addChoices(
                                { name: 'Rock (Pathar) 🪨', value: 'rock' },
                                { name: 'Paper (Kagaz) 📄', value: 'paper' },
                                { name: 'Scissors (Kainchi) ✂️', value: 'scissors' }
                            )))
            .addSubcommand(subcommand =>
                subcommand.setName('truthdare')
                    .setDescription('Get a Hinglish Truth or Dare challenge 🤫🔥')
                    .addStringOption(option =>
                        option.setName('type')
                            .setDescription('Truth chahiye ya Dare?')
                            .setRequired(true)
                            .addChoices(
                                { name: 'Truth (Sach) 🤫', value: 'truth' },
                                { name: 'Dare (Chunauti) 🔥', value: 'dare' }
                            )))
    ];

    if (process.env.DISCORD_TOKEN) {
        const rest = new REST({ version: '10' }).setToken(process.env.DISCORD_TOKEN);
        try {
            console.log('Started refreshing application (/) commands.');
            await rest.put(
                Routes.applicationCommands(discordClient.user.id),
                { body: commands }
            );
            console.log('Successfully reloaded application (/) commands.');
        } catch (error) {
            console.error('Error registering slash commands:', error.message);
        }
    }
});

// --- Slash Command Handler ---
discordClient.on('interactionCreate', async interaction => {
    if (!interaction.isChatInputCommand()) return;

    if (interaction.commandName === 'mood') {
        const selectedMood = interaction.options.getString('type');
        db.globalMood = selectedMood;
        saveDB();
        await interaction.reply(`Mera mood ab **${selectedMood.toUpperCase()}** ho gaya hai! 🔥`);
        return;
    }

    if (interaction.commandName === 'bhaichara') {
        const userId = interaction.user.id;
        const userStats = db.users[userId] || { xp: 0, level: 1 };
        await interaction.reply(`**<@${userId}> ka Bhaichara Level:**\n⭐ Level: **${userStats.level}**\n📈 XP: **${userStats.xp}**`);
        return;
    }

    if (interaction.commandName === 'leaderboard') {
        const usersArray = [];
        for (const [userId, stats] of Object.entries(db.users || {})) {
            usersArray.push({ userId, xp: stats.xp, level: stats.level });
        }

        if (usersArray.length === 0) {
            await interaction.reply("Bhai abhi tak kisi ka Bhaichara account nahi bana hai. Pehle baatein karo! 🗣️");
            return;
        }

        // Sort by Level desc, then by XP desc
        usersArray.sort((a, b) => b.level !== a.level ? b.level - a.level : b.xp - a.xp);

        const top10 = usersArray.slice(0, 10);
        let leaderboardText = "🏆 **BHAICHARA LEADERBOARD (Top 10)** 🏆\n\n";
        
        const medals = ["🥇", "🥈", "🥉", "4.", "5.", "6.", "7.", "8.", "9.", "10."];
        top10.forEach((user, index) => {
            const medal = medals[index] || `${index + 1}.`;
            leaderboardText += `${medal} <@${user.userId}> — **Level ${user.level}** (XP: ${user.xp})\n`;
        });

        await sendSafeReply(interaction, leaderboardText);
        return;
    }

    if (interaction.commandName === 'givebhaichara') {
        const senderId = interaction.user.id;
        const targetUser = interaction.options.getUser('target');
        const amount = interaction.options.getInteger('amount');

        if (targetUser.id === senderId) {
            await interaction.reply("Bhai khud ko hi Bhaichara gift karega? Aise nahi chalta! ❌");
            return;
        }

        if (targetUser.bot) {
            await interaction.reply("Bhai bots ke paas Bhaichara nahi hota, hum sirf rishtey banate hain! 🤖");
            return;
        }

        const senderStats = db.users[senderId] || { xp: 0, level: 1 };
        db.users[senderId] = senderStats;

        if (senderStats.xp < amount) {
            await interaction.reply(`Bhai tere paas itna XP nahi hai! Tere paas sirf **${senderStats.xp} XP** hai. 😢`);
            return;
        }

        // Deduct from sender
        senderStats.xp -= amount;
        
        // Add to target
        const targetStats = db.users[targetUser.id] || { xp: 0, level: 1 };
        targetStats.xp += amount;
        db.users[targetUser.id] = targetStats;

        saveDB();

        await interaction.reply(`🤝 **Bhaichara Transfer Successful!**\n<@${senderId}> ne <@${targetUser.id}> ko **${amount} XP** gift kiya! Bhaichara zindabad! 🥂`);
        return;
    }

    if (interaction.commandName === 'game') {
        const subcommand = interaction.options.getSubcommand();

        if (subcommand === 'rps') {
            const userChoice = interaction.options.getString('choice');
            const choices = ['rock', 'paper', 'scissors'];
            const botChoice = choices[Math.floor(Math.random() * choices.length)];

            const emojiMap = { rock: '🪨', paper: '📄', scissors: '✂️' };
            const nameMap = { rock: 'Rock', paper: 'Paper', scissors: 'Scissors' };

            let result = "";
            if (userChoice === botChoice) {
                result = "Match Draw ho gaya bhai! 🤝 Dono ne same choose kiya.";
            } else if (
                (userChoice === 'rock' && botChoice === 'scissors') ||
                (userChoice === 'paper' && botChoice === 'rock') ||
                (userChoice === 'scissors' && botChoice === 'paper')
            ) {
                result = "🎉 **Arey waah! Tu jeet gaya!** Party de ab. 🥳";
                const userId = interaction.user.id;
                if (!db.users[userId]) db.users[userId] = { xp: 0, level: 1 };
                db.users[userId].xp += 50;
                saveDB();
                result += " (+50 Bhaichara XP)";
            } else {
                result = "😂 **Haha! Main jeet gaya!** Aur seekh ke aa. 🧠";
            }

            await interaction.reply(`**Rock-Paper-Scissors!**\n👤 Tera choice: **${nameMap[userChoice]} ${emojiMap[userChoice]}**\n🤖 Mera choice: **${nameMap[botChoice]} ${emojiMap[botChoice]}**\n\n${result}`);
            return;
        }

        if (subcommand === 'truthdare') {
            const type = interaction.options.getString('type');
            await interaction.deferReply();

            try {
                const prompt = type === 'truth'
                    ? "Generate a funny, interesting, slightly embarrassing 'Truth' question for a Discord user in Hinglish. Keep it casual, friendly, and short (1-2 sentences)."
                    : "Generate a funny, harmless, but challenging 'Dare' task for a Discord user in Hinglish. Keep it creative and short (1-2 sentences).";
                
                const reply = await getAIResponse(prompt);
                const safeReply = fixMentions(reply);
                await sendSafeReply(interaction, `🤫 **Truth or Dare!** (${type.toUpperCase()})\n\n👉 <@${interaction.user.id}>, tere liye yeh raha:\n**"${safeReply}"**`);
            } catch (err) {
                console.error("Truth/Dare generation failed:", err.message);
                const defaultTruths = [
                    "Bhai, teri sabse badi single-life insecurity kya hai? Sach bolna!",
                    "Tune kabhi kisi dost ke profile pe stalk kiya hai aur galti se purani photo like ki hai?",
                    "Apne school/college ka sabse bada embarrassing moment bata."
                ];
                const defaultDares = [
                    "Is server ke admin ko ek flirty message bhejo aur screenshot chat me dalo!",
                    "Agli 3 messages me sirf shuddh Hindi me baat karo bina English letters ke.",
                    "Apna status badalke 'I love PrimeBot' karlo agle 10 minute ke liye."
                ];
                const backupList = type === 'truth' ? defaultTruths : defaultDares;
                const randomBackup = backupList[Math.floor(Math.random() * backupList.length)];
                await interaction.editReply(`🤫 **Truth or Dare!** (${type.toUpperCase()})\n\n👉 <@${interaction.user.id}>, tere liye yeh raha:\n**"${randomBackup}"**`);
            }
            return;
        }
    }

    if (interaction.commandName === 'roast' || interaction.commandName === 'joke') {
        await interaction.deferReply();
        try {
            let promptText = "";
            if (interaction.commandName === 'roast') {
                const targetUser = interaction.options.getUser('target');
                promptText = `You are a savage Indian Discord bot. Roast <@${targetUser.id}> extremely brutally and funnily in natural Hinglish. Keep it short (2-3 lines).`;
            } else {
                promptText = "Tell a very funny Hinglish joke (Hindi written in English) that an Indian teenager would laugh at. Keep it short.";
            }

            let reply = await getAIResponse(promptText);
            reply = fixMentions(reply);
            await sendSafeReply(interaction, reply);
        } catch (e) {
            console.error("Roast/Joke failed:", e.message);
            await interaction.editReply("Bhai abhi mera dimaag kharab hai, baad mein baat karna. 🤦‍♂️");
        }
        return;
    }

    if (interaction.commandName === 'imagine') {
        const prompt = interaction.options.getString('prompt');
        await interaction.deferReply();

        try {
            const encodedPrompt = encodeURIComponent(prompt);
            const imageUrl = `https://image.pollinations.ai/prompt/${encodedPrompt}?width=1024&height=1024&nologo=true`;
            const attachment = new AttachmentBuilder(imageUrl, { name: 'generated-image.png' });

            await interaction.editReply({ 
                content: `**Prompt:** ${prompt}\n*Generated by PrimeBot* ✨`, 
                files: [attachment] 
            });
        } catch (error) {
            console.error('Image generation error:', error.message);
            await interaction.editReply('Bhai image generate karne mein dikkat aa gayi. Baad mein try karna! 😢');
        }
    }
});

// Queue for sequential message processing per user
const userQueues = new Map();

// Rate limiting to prevent API abuse
const userMessageTimestamps = new Map();

// Periodic cleanup of stale rate limits (every 10 minutes)
setInterval(() => {
    const now = Date.now();
    for (const [userId, timestamps] of userMessageTimestamps.entries()) {
        const valid = timestamps.filter(t => now - t < 60000);
        if (valid.length === 0) {
            userMessageTimestamps.delete(userId);
        } else {
            userMessageTimestamps.set(userId, valid);
        }
    }
}, 10 * 60 * 1000);

// --- Message Handler ---
discordClient.on('messageCreate', async (message) => {
    if (message.author.bot) return;

    const userId = message.author.id;

    // --- Spam Protection / Rate Limiting ---
    if (!userMessageTimestamps.has(userId)) {
        userMessageTimestamps.set(userId, []);
    }
    let timestamps = userMessageTimestamps.get(userId);
    const nowStamp = Date.now();
    
    // Keep only timestamps from last 10 seconds
    timestamps = timestamps.filter(t => nowStamp - t < 10000);
    timestamps.push(nowStamp);
    userMessageTimestamps.set(userId, timestamps);

    if (timestamps.length > 5) {
        if (timestamps.length === 6) {
            await message.reply("Bhai thoda aaram se! Spam mat kar, system crash ho jayega mera 🛑");
        }
        return;
    }
    // ---------------------------------------

    // Abuse detection and timeout (1 min)
    const abusiveWords = ['chutiya', 'bhosdike', 'madarchod', 'bhenchod', 'gandu', 'kutta', 'kamina', 'saale', 'harami', 'fuck', 'bitch', 'asshole', 'motherfucker', 'bastard', 'lodu', 'loda', 'chut'];
    const containsAbuse = abusiveWords.some(word => new RegExp(`\\b${word}\\b`, 'i').test(message.content));

    if (containsAbuse) {
        if (message.guild && message.member) {
            try {
                await message.member.timeout(60 * 1000, "Using abusive language");
                await message.reply("Bhai gaali-galoch nahi! 1 minute ka timeout le aur thanda ho ja 🤐");
            } catch (error) {
                console.error("Timeout failed (missing permissions or higher role):", error.message);
                await message.reply("Gaali kyu de raha hai bhai? Tameez se baat kar 😤");
            }
        } else {
            await message.reply("Bhai tameez se baat kar, gaali mat de! 🤐");
        }
        return;
    }

    // --- XP & Bhaichara System ---
    if (!db.users[userId]) {
        db.users[userId] = { xp: 0, level: 1 };
    }
    
    const xpGained = Math.floor(Math.random() * 11) + 15;
    db.users[userId].xp += xpGained;
    
    const nextLevelXp = db.users[userId].level * 500;
    if (db.users[userId].xp >= nextLevelXp) {
        db.users[userId].level += 1;
        saveDB();
        try {
            await message.channel.send(`Arey oye! 🎉 <@${userId}> ka **Bhaichara Level** ab **${db.users[userId].level}** ho gaya hai! Party kab de raha hai bhai? 🥂`);
        } catch(e) {}
    } else {
        saveDB();
    }
    // -----------------------------

    // --- Auto Reactions ---
    const msgLower = message.content.toLowerCase();
    const reactions = {
        "lol": "😂", "lmao": "🤣", "op": "🔥", "gg": "🤝",
        "sad": "😢", "bhai": "🫂", "hmm": "🤔"
    };
    for (const [word, emoji] of Object.entries(reactions)) {
        if (msgLower.includes(word)) {
            try { await message.react(emoji); } catch (e) {}
            break;
        }
    }
    // ----------------------

    // Check if bot should respond:
    // 1. Direct Message (DM)
    // 2. Mentioned in server
    // 3. Replied to bot message in server
    const isDM = !message.guild;
    const isMentioned = message.mentions.has(discordClient.user);
    
    let isReply = false;
    if (message.reference && message.reference.messageId) {
        try {
            const referencedMsg = await message.channel.messages.fetch(message.reference.messageId);
            if (referencedMsg && referencedMsg.author.id === discordClient.user.id) {
                isReply = true;
            }
        } catch (error) {
            console.error("Error fetching reference message:", error.message);
        }
    }
    
    if (!isDM && !isMentioned && !isReply) return;

    // Initialize user queue for sequential processing
    if (!userQueues.has(userId)) {
        userQueues.set(userId, Promise.resolve());
    }

    const currentQueue = userQueues.get(userId);
    const nextTask = currentQueue.then(async () => {
        let cleanMessage = message.content.replace(new RegExp(`<@!?${discordClient.user.id}>`, 'g'), '').trim();
        if (!cleanMessage && message.attachments.size === 0) cleanMessage = "Hello!";

        // Load / Filter history (24 hours window)
        if (!userConversations.has(userId)) {
            userConversations.set(userId, []);
        }
        let history = userConversations.get(userId) || [];
        const now = Date.now();
        const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000;

        history = history.filter(msg => msg && msg.timestamp && (now - msg.timestamp < TWENTY_FOUR_HOURS));

        if (history.length > 50) {
            history = history.slice(history.length - 50);
        }

        // --- URL Extraction & Web Scraping ---
        let urlInfo = "";
        const urls = cleanMessage.match(/(https?:\/\/[^\s]+)/gi);
        if (urls && urls.length > 0) {
            if (process.env.DEV_MODE !== 'true') {
                try { await message.react('🔍'); } catch(e){}
            }
            
            for (const url of urls.slice(0, 2)) {
                const webText = await extractUrlText(url);
                urlInfo += `\n[Content of URL (${url}): ${webText}]\n`;
            }
        }

        let finalUserMessage = cleanMessage;
        if (urlInfo) {
            finalUserMessage += `\n\nUser also shared the following web page content:\n${urlInfo}\nPlease read and comment/summarize as requested.`;
        }

        let userParts = [];
        if (finalUserMessage) {
            userParts.push({ text: finalUserMessage });
        }

        // Process image / audio attachments
        if (message.attachments.size > 0) {
            for (const attachment of message.attachments.values()) {
                const contentType = attachment.contentType || '';
                const isImage = contentType.startsWith('image/');
                const isAudio = contentType.startsWith('audio/');
                
                if (isImage || isAudio) {
                    try {
                        const response = await fetch(attachment.url);
                        const buffer = await response.arrayBuffer();
                        const base64 = Buffer.from(buffer).toString('base64');
                        userParts.push({
                            inlineData: {
                                data: base64,
                                mimeType: contentType
                            }
                        });
                    } catch (err) {
                        console.error("Error fetching attachment:", err.message);
                    }
                }
            }
        }

        if (userParts.length === 0) {
            userParts.push({ text: "Hello!" });
        }

        // Create the pending history payload
        const currentTurn = { role: 'user', parts: userParts, timestamp: now };
        const candidateHistory = [...history, currentTurn];

        try {
            if (process.env.DEV_MODE !== 'true') {
                await message.channel.sendTyping();
            }

            const responseText = await getAIResponse(candidateHistory);

            if (responseText) {
                let replyText = fixMentions(responseText);

                // Commit both user and model turns to history
                history.push(currentTurn);
                history.push({ role: 'model', parts: [{ text: replyText }], timestamp: Date.now() });
                userConversations.set(userId, history);
                saveHistory();

                if (process.env.DEV_MODE === 'true') {
                    console.log(`[DEV_MODE] Bot would have replied: "${replyText}"`);
                    return;
                }

                // Simulate human typing delay
                const typingDelayMs = Math.min(Math.max(replyText.length * 35, 800), 5000);
                if (process.env.DEV_MODE !== 'true') {
                    await message.channel.sendTyping();
                }
                await new Promise(resolve => setTimeout(resolve, typingDelayMs));

                await sendSafeReply(message, replyText);
            } else {
                throw new Error("Empty response from AI helper");
            }

        } catch (error) {
            console.error('Final Error Handler:', error);
            
            if (process.env.DEV_MODE === 'true') {
                console.log(`[DEV_MODE ERROR] Bot error: "${error.message}"`);
                return;
            }

            const isQuota = error.status === 429 || (error.message && error.message.includes('Quota'));
            if (isQuota) {
                await message.reply("Bhai saari keys ki limit khatam ho gayi hai! Kal try karna padega. 😭");
            } else {
                await message.reply("Yaar, kuch glitch aa raha hai, thodi der baad try karo! 😅");
            }
        }
    }).catch(console.error);

    userQueues.set(userId, nextTask);
});

// --- Welcome Message Event Handler ---
discordClient.on('guildMemberAdd', async (member) => {
    // Try to find typical server welcome channels
    const channel = member.guild.systemChannel || member.guild.channels.cache.find(ch => 
        ch.name && (ch.name.includes('welcome') || ch.name.includes('general') || ch.name.includes('chat'))
    );
    if (!channel) return;

    try {
        const welcomePrompts = [
            `Create a funny, super casual Hinglish welcome message welcoming <@${member.id}> to the server. Act like a chill Indian friend saying 'Welcome to the gang!'. Keep it short (2 sentences).`,
            `Create a light roast style Hinglish welcome message for <@${member.id}> who just joined the server. Make it hilarious but friendly. Keep it short (2 sentences).`,
            `Create a warm, cool Hinglish welcome greeting for <@${member.id}> joining the server. Keep it short (2 sentences).`
        ];
        const randomPrompt = welcomePrompts[Math.floor(Math.random() * welcomePrompts.length)];
        const welcomeMessageText = await getAIResponse(randomPrompt);
        await channel.send(fixMentions(welcomeMessageText));
    } catch (err) {
        console.error("Error sending welcome message:", err.message);
        try {
            await channel.send(`Arey oye! 🎉 <@${member.id}> aa gaya! Swagat karo inka gang me! 🥂`);
        } catch (e) {}
    }
});

// Start the bot
if (process.env.DISCORD_TOKEN) {
    discordClient.login(process.env.DISCORD_TOKEN);
} else {
    console.error("DISCORD_TOKEN is not defined in environment.");
}
