const express = require('express');
const router = express.Router();
const { v4: uuidv4 } = require('uuid');
const { db } = require('../db');
const authMiddleware = require('../middleware/auth');

router.use(authMiddleware);

const STATUS_FLOW = {
  pending: 'confirmed',
  confirmed: 'preparing',
  preparing: 'on_way',
  on_way: 'delivered',
  delivered: null,
  cancelled: null,
};

// GET /api/orders
router.get('/', (req, res) => {
  const { status, search, channel } = req.query;
  const restaurant_id = req.user.restaurant_id;

  let query = `
    SELECT o.*, c.name as customer_name, c.platform as customer_platform
    FROM orders o
    JOIN customers c ON o.customer_id = c.id
    WHERE o.restaurant_id = ?
  `;
  const params = [restaurant_id];

  if (status && status !== 'all') {
    query += ' AND o.status = ?';
    params.push(status);
  }

  if (channel && channel !== 'all') {
    query += ' AND o.channel = ?';
    params.push(channel);
  }

  if (search) {
    query += ' AND (c.name LIKE ? OR o.id LIKE ?)';
    params.push(`%${search}%`, `%${search}%`);
  }

  query += ' ORDER BY o.created_at DESC';

  const orders = db.prepare(query).all(...params);
  res.json(orders);
});

// GET /api/orders/:id
router.get('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const order = db.prepare(`
    SELECT o.*, c.name as customer_name, c.phone as customer_phone, c.platform as customer_platform
    FROM orders o
    JOIN customers c ON o.customer_id = c.id
    WHERE o.id = ? AND o.restaurant_id = ?
  `).get(id, restaurant_id);

  if (!order) {
    return res.status(404).json({ error: 'Order not found' });
  }

  const items = db.prepare('SELECT * FROM order_items WHERE order_id = ?').all(id);
  order.items = items;

  res.json(order);
});

// POST /api/orders
router.post('/', (req, res) => {
  const { customer_id, channel, type, total, address, notes, items } = req.body;
  const restaurant_id = req.user.restaurant_id;

  if (!customer_id || !total) {
    return res.status(400).json({ error: 'customer_id and total are required' });
  }

  const id = uuidv4();
  db.prepare(`
    INSERT INTO orders (id, restaurant_id, customer_id, channel, type, total, address, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(id, restaurant_id, customer_id, channel || 'telegram', type || 'delivery', total, address || null, notes || null);

  if (items && items.length > 0) {
    const insertItem = db.prepare(`
      INSERT INTO order_items (id, order_id, product_id, name, price, quantity)
      VALUES (?, ?, ?, ?, ?, ?)
    `);
    for (const item of items) {
      insertItem.run(uuidv4(), id, item.product_id || null, item.name, item.price, item.quantity || 1);
    }
  }

  // Update customer stats
  db.prepare(`
    UPDATE customers
    SET total_orders = total_orders + 1,
        total_spent = total_spent + ?,
        last_seen = CURRENT_TIMESTAMP
    WHERE id = ?
  `).run(total, customer_id);

  const order = db.prepare('SELECT * FROM orders WHERE id = ?').get(id);
  res.status(201).json(order);
});

// PATCH /api/orders/:id/status
router.patch('/:id/status', (req, res) => {
  const { id } = req.params;
  const { action } = req.body; // 'advance' or 'cancel'
  const restaurant_id = req.user.restaurant_id;

  const order = db.prepare('SELECT * FROM orders WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!order) {
    return res.status(404).json({ error: 'Order not found' });
  }

  let newStatus;
  if (action === 'cancel') {
    newStatus = 'cancelled';
  } else {
    newStatus = STATUS_FLOW[order.status];
    if (!newStatus) {
      return res.status(400).json({ error: `Cannot advance from status: ${order.status}` });
    }
  }

  db.prepare('UPDATE orders SET status = ? WHERE id = ?').run(newStatus, id);
  const updated = db.prepare('SELECT * FROM orders WHERE id = ?').get(id);
  res.json(updated);
});

// PATCH /api/orders/:id
router.patch('/:id', (req, res) => {
  const { id } = req.params;
  const { notes, address } = req.body;
  const restaurant_id = req.user.restaurant_id;

  const order = db.prepare('SELECT * FROM orders WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!order) {
    return res.status(404).json({ error: 'Order not found' });
  }

  db.prepare('UPDATE orders SET notes = ?, address = ? WHERE id = ?').run(
    notes !== undefined ? notes : order.notes,
    address !== undefined ? address : order.address,
    id
  );

  const updated = db.prepare('SELECT * FROM orders WHERE id = ?').get(id);
  res.json(updated);
});

// DELETE /api/orders/:id
router.delete('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const order = db.prepare('SELECT * FROM orders WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!order) {
    return res.status(404).json({ error: 'Order not found' });
  }

  db.prepare('DELETE FROM order_items WHERE order_id = ?').run(id);
  db.prepare('DELETE FROM orders WHERE id = ?').run(id);

  res.json({ message: 'Order deleted successfully' });
});

module.exports = router;
