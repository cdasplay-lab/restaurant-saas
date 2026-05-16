const express = require('express');
const router = express.Router();
const { v4: uuidv4 } = require('uuid');
const { db } = require('../db');
const authMiddleware = require('../middleware/auth');

router.use(authMiddleware);

// GET /api/products
router.get('/', (req, res) => {
  const { category } = req.query;
  const restaurant_id = req.user.restaurant_id;

  let query = 'SELECT * FROM products WHERE restaurant_id = ?';
  const params = [restaurant_id];

  if (category && category !== 'all') {
    query += ' AND category = ?';
    params.push(category);
  }

  query += ' ORDER BY category, name';

  const products = db.prepare(query).all(...params);
  res.json(products);
});

// GET /api/products/:id
router.get('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const product = db.prepare('SELECT * FROM products WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }

  res.json(product);
});

// POST /api/products
router.post('/', (req, res) => {
  const { name, price, category, description, icon, variants } = req.body;
  const restaurant_id = req.user.restaurant_id;

  if (!name || price === undefined) {
    return res.status(400).json({ error: 'name and price are required' });
  }

  const id = uuidv4();
  db.prepare(`
    INSERT INTO products (id, restaurant_id, name, price, category, description, icon, variants)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    id, restaurant_id, name, price,
    category || 'main',
    description || null,
    icon || '🍔',
    variants ? JSON.stringify(variants) : '[]'
  );

  const product = db.prepare('SELECT * FROM products WHERE id = ?').get(id);
  res.status(201).json(product);
});

// PATCH /api/products/:id
router.patch('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const product = db.prepare('SELECT * FROM products WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }

  const { name, price, category, description, icon, variants, available } = req.body;

  db.prepare(`
    UPDATE products SET
      name = ?,
      price = ?,
      category = ?,
      description = ?,
      icon = ?,
      variants = ?,
      available = ?
    WHERE id = ?
  `).run(
    name !== undefined ? name : product.name,
    price !== undefined ? price : product.price,
    category !== undefined ? category : product.category,
    description !== undefined ? description : product.description,
    icon !== undefined ? icon : product.icon,
    variants !== undefined ? JSON.stringify(variants) : product.variants,
    available !== undefined ? (available ? 1 : 0) : product.available,
    id
  );

  const updated = db.prepare('SELECT * FROM products WHERE id = ?').get(id);
  res.json(updated);
});

// PATCH /api/products/:id/availability
router.patch('/:id/availability', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const product = db.prepare('SELECT * FROM products WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }

  const newAvailable = product.available ? 0 : 1;
  db.prepare('UPDATE products SET available = ? WHERE id = ?').run(newAvailable, id);

  const updated = db.prepare('SELECT * FROM products WHERE id = ?').get(id);
  res.json(updated);
});

// DELETE /api/products/:id
router.delete('/:id', (req, res) => {
  const { id } = req.params;
  const restaurant_id = req.user.restaurant_id;

  const product = db.prepare('SELECT * FROM products WHERE id = ? AND restaurant_id = ?').get(id, restaurant_id);
  if (!product) {
    return res.status(404).json({ error: 'Product not found' });
  }

  db.prepare('DELETE FROM products WHERE id = ?').run(id);
  res.json({ message: 'Product deleted successfully' });
});

module.exports = router;
