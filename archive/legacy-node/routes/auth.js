const express = require('express');
const router = express.Router();
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const { db } = require('../db');
const authMiddleware = require('../middleware/auth');

// POST /api/auth/login
router.post('/login', (req, res) => {
  const { email, password } = req.body;

  if (!email || !password) {
    return res.status(400).json({ error: 'Email and password are required' });
  }

  const user = db.prepare('SELECT * FROM users WHERE email = ?').get(email);
  if (!user) {
    return res.status(401).json({ error: 'Invalid email or password' });
  }

  const valid = bcrypt.compareSync(password, user.password_hash);
  if (!valid) {
    return res.status(401).json({ error: 'Invalid email or password' });
  }

  const restaurant = db.prepare('SELECT * FROM restaurants WHERE id = ?').get(user.restaurant_id);

  const token = jwt.sign(
    {
      id: user.id,
      email: user.email,
      restaurant_id: user.restaurant_id,
      role: user.role,
    },
    process.env.JWT_SECRET || 'change_this_secret_123',
    { expiresIn: '24h' }
  );

  res.json({
    token,
    user: {
      id: user.id,
      email: user.email,
      name: user.name,
      role: user.role,
      restaurant_id: user.restaurant_id,
    },
    restaurant: {
      id: restaurant.id,
      name: restaurant.name,
      plan: restaurant.plan,
    },
  });
});

// POST /api/auth/logout
router.post('/logout', (req, res) => {
  res.json({ message: 'Logged out successfully' });
});

// GET /api/auth/me
router.get('/me', authMiddleware, (req, res) => {
  const user = db.prepare('SELECT id, email, name, role, restaurant_id FROM users WHERE id = ?').get(req.user.id);
  if (!user) {
    return res.status(404).json({ error: 'User not found' });
  }

  const restaurant = db.prepare('SELECT * FROM restaurants WHERE id = ?').get(user.restaurant_id);

  res.json({
    user,
    restaurant,
  });
});

module.exports = router;
