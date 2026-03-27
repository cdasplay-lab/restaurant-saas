const express = require('express');
const router = express.Router();
const { v4: uuidv4 } = require('uuid');
const { db } = require('../db');
const authMiddleware = require('../middleware/auth');
const { generateBotReply } = require('../services/openai');

router.use(authMiddleware);

// GET /api/conversations
router.get('/', (req, res) => {
  const { mode, status } = req.query;
  const restaurant_id = req.user.restaurant_id;

  let query = `
    SELECT conv.*, c.name as customer_name, c.platform as customer_platform,
      (SELECT content FROM messages WHERE conversation_id = conv.id ORDER BY created_at DESC LIMIT 1) as last_message,
      (SELECT created_at FROM messages WHERE conversation_id = conv.id ORDER BY created_at DESC LIMIT 1) as last_message_at
    FROM conversations conv
    JOIN customers c ON conv.customer_id = c.id
    WHERE conv.restaurant_id = ?
  `;
  const params = [restaurant_id];

  if (mode && mode !== 'all') {
    query += ' AND conv.mode = ?';
    params.push(mode);
  }

  if (status && status !== 'all') {
    query += ' AND conv.status = ?';
    params.push(status);
  }

  query += ' ORDER BY conv.updated_at DESC';

  const conversations = db.prepare(query).all(...params);
  res.json(conversations);
});

// GET /api/conversations/:id/messages
router.get('/:id/messages', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const conv = db.prepare('SELECT * FROM conversations WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!conv) {
    return res.status(404).json({ error: 'Conversation not found' });
  }

  const messages = db.prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC').all(id);
  res.json(messages);
});

// POST /api/conversations/:id/messages
router.post('/:id/messages', (req, res) => {
  const { id } = req.params;
  const { content } = req.body;
  const restaurant_id = req.user.restaurant_id;

  if (!content) {
    return res.status(400).json({ error: 'Content is required' });
  }

  const conv = db.prepare('SELECT * FROM conversations WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!conv) {
    return res.status(404).json({ error: 'Conversation not found' });
  }

  const msgId = uuidv4();
  db.prepare('INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)').run(
    msgId, id, 'staff', content
  );

  db.prepare('UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?').run(id);

  const message = db.prepare('SELECT * FROM messages WHERE id = ?').get(msgId);
  res.status(201).json(message);
});

// PATCH /api/conversations/:id/mode
router.patch('/:id/mode', (req, res) => {
  const { id } = req.params;
  const { mode } = req.body;
  const restaurant_id = req.user.restaurant_id;

  const conv = db.prepare('SELECT * FROM conversations WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!conv) {
    return res.status(404).json({ error: 'Conversation not found' });
  }

  const newMode = mode || (conv.mode === 'bot' ? 'human' : 'bot');
  db.prepare('UPDATE conversations SET mode = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?').run(newMode, id);

  const updated = db.prepare('SELECT * FROM conversations WHERE id = ?').get(id);
  res.json(updated);
});

// PATCH /api/conversations/:id/urgent
router.patch('/:id/urgent', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const conv = db.prepare('SELECT * FROM conversations WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!conv) {
    return res.status(404).json({ error: 'Conversation not found' });
  }

  const newUrgent = conv.urgent ? 0 : 1;
  db.prepare('UPDATE conversations SET urgent = ? WHERE id = ?').run(newUrgent, id);

  const updated = db.prepare('SELECT * FROM conversations WHERE id = ?').get(id);
  res.json(updated);
});

// PATCH /api/conversations/:id/read
router.patch('/:id/read', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const conv = db.prepare('SELECT * FROM conversations WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!conv) {
    return res.status(404).json({ error: 'Conversation not found' });
  }

  db.prepare('UPDATE conversations SET unread_count = 0 WHERE id = ?').run(id);

  const updated = db.prepare('SELECT * FROM conversations WHERE id = ?').get(id);
  res.json(updated);
});

// POST /api/conversations/:id/ai-reply
router.post('/:id/ai-reply', async (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const conv = db.prepare('SELECT * FROM conversations WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!conv) {
    return res.status(404).json({ error: 'Conversation not found' });
  }

  const restaurant = db.prepare('SELECT * FROM restaurants WHERE id = ?').get(restaurant_id);
  const messages = db.prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC').all(id);

  const reply = await generateBotReply(messages, restaurant ? restaurant.name : 'Al-Rashid Kitchen');

  if (!reply) {
    return res.status(500).json({ error: 'Failed to generate AI reply' });
  }

  const msgId = uuidv4();
  db.prepare('INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)').run(
    msgId, id, 'bot', reply
  );

  db.prepare('UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?').run(id);

  const message = db.prepare('SELECT * FROM messages WHERE id = ?').get(msgId);
  res.status(201).json(message);
});

module.exports = router;
