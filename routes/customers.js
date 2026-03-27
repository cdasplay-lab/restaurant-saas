const express = require('express');
const router = express.Router();
const { db } = require('../db');
const authMiddleware = require('../middleware/auth');

router.use(authMiddleware);

// GET /api/customers
router.get('/', (req, res) => {
  const { search } = req.query;
  const restaurant_id = req.user.restaurant_id;

  let query = 'SELECT * FROM customers WHERE restaurant_id = ?';
  const params = [restaurant_id];

  if (search) {
    query += ' AND (name LIKE ? OR phone LIKE ?)';
    params.push(`%${search}%`, `%${search}%`);
  }

  query += ' ORDER BY total_spent DESC';

  const customers = db.prepare(query).all(...params);
  res.json(customers);
});

// GET /api/customers/:id
router.get('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const customer = db.prepare('SELECT * FROM customers WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!customer) {
    return res.status(404).json({ error: 'Customer not found' });
  }

  const orders = db.prepare(`
    SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC LIMIT 10
  `).all(id);

  customer.orders = orders;

  res.json(customer);
});

// PATCH /api/customers/:id
router.patch('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const customer = db.prepare('SELECT * FROM customers WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!customer) {
    return res.status(404).json({ error: 'Customer not found' });
  }

  const { vip, preferences, favorite_item, name, phone } = req.body;

  db.prepare(`
    UPDATE customers SET
      vip = ?,
      preferences = ?,
      favorite_item = ?,
      name = ?,
      phone = ?
    WHERE id = ?
  `).run(
    vip !== undefined ? (vip ? 1 : 0) : customer.vip,
    preferences !== undefined ? preferences : customer.preferences,
    favorite_item !== undefined ? favorite_item : customer.favorite_item,
    name !== undefined ? name : customer.name,
    phone !== undefined ? phone : customer.phone,
    id
  );

  const updated = db.prepare('SELECT * FROM customers WHERE id = ?').get(id);
  res.json(updated);
});

// DELETE /api/customers/:id
router.delete('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const customer = db.prepare('SELECT * FROM customers WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!customer) {
    return res.status(404).json({ error: 'Customer not found' });
  }

  db.prepare('DELETE FROM customers WHERE id = ?').run(id);
  res.json({ message: 'Customer deleted successfully' });
});

module.exports = router;
