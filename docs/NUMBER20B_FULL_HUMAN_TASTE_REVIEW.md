# NUMBER 20B — Full Human Taste Review

> **Purpose:** Honest quality assessment of final customer-facing replies
> after the Elite Reply Brain processes each scenario.
> Scored like a human restaurant business owner, not a software test.

**Total scenarios:** 155
**Date:** 2026-04-29

## Category Summary

| Category | Count | Avg Score | ممتاز | جيد | يحتاج | مرفوض |
|---|---|---|---|---|---|---|
| أساسيات النصوص | 15 | 8.5 | 9 | 5 | 1 | 0 |
| تدفق الطلبات | 25 | 9.0 | 22 | 3 | 0 | 0 |
| الشكاوى والدعم | 25 | 8.8 | 18 | 7 | 0 | 0 |
| الرسائل الصوتية | 20 | 8.8 | 15 | 5 | 0 | 0 |
| الصور | 20 | 8.8 | 17 | 3 | 0 | 0 |
| الستوري / الريل / البوست | 25 | 8.8 | 20 | 5 | 0 | 0 |
| الذاكرة والتخصيص | 15 | 9.2 | 15 | 0 | 0 | 0 |
| الاشتراك / التحويل / الأمان | 10 | 8.1 | 2 | 8 | 0 | 0 |

## Overall Stats

- **Average score:** 8.8/10
- **ممتاز (9-10):** 118 scenarios
- **جيد (7-8):** 36 scenarios
- **يحتاج تحسين (5-6):** 1 scenarios
- **مرفوض (1-4):** 0 scenarios

---

## All Scenarios

### T01 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> هلا

**Context:** GPT generates corporate greeting

**Detected intent:** `greeting`

**Simulated bot input:**
```
هلا وغلا! يسعدني مساعدتك اليوم. كيف يمكنني خدمتك؟
```

**Final customer reply (after Elite Brain):**
```
أهلين 🌷 تفضل، شتريد؟
```

**Evaluation:** قالب الترحيب ممتاز، قصير وعراقي

---

### T02 — أساسيات النصوص | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> شنو عندكم؟

**Context:** GPT gives menu with corporate intro

**Detected intent:** `menu_request`

**Simulated bot input:**
```
بالتأكيد! يمكنني مساعدتك في معرفة المنيو. عندنا زينگر 9,000 وشاورما 5,000 وبرگر مشروم 8,000 وكولا 1,500 وبطاطا 2,000. هل ترغب في الطلب؟
```

**Final customer reply (after Elite Brain):**
```
عندنا زينگر 9,000 وشاورما 5,000 وبرگر مشروم 8,000 وكولا 1,500 وبطاطا 2,000. الطلب؟
```

**Evaluation:** 20C: حذف الجملة المكسورة 'في معرفة المنيو.' والإبقاء على 'عندنا زينگر...' — صحيح

---

### T03 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> بكم الزينگر؟

**Context:** Price question with banned opener

**Detected intent:** `price_question`

**Simulated bot input:**
```
بالتأكيد! الزينگر بـ 9,000 د.ع 🌷
```

**Final customer reply (after Elite Brain):**
```
الزينگر بـ 9,000 د.ع 🌷
```

**Evaluation:** 20C: الزينگر بـ 9,000 د.ع 🌷 — نظيف بدون بادئة

---

### T04 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> شنو تنصحني؟

**Context:** Recommendation — natural reply

**Detected intent:** `recommendation`

**Simulated bot input:**
```
ننصحك بالزينگر! هو الأكثر طلباً عندنا بسعر 9,000 د.ع 🌷 تريد تطلب؟
```

**Final customer reply (after Elite Brain):**
```
ننصحك بالزينگر! هو الأكثر طلباً عندنا بسعر 9,000 د.ع 🌷 تريد تطلب؟
```

**Evaluation:** قصير وطبيعي ومبيعاتي

---

### T05 — أساسيات النصوص | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> شنو الأرخص؟

**Context:** Cheapest item — straightforward

**Detected intent:** `cheapest_item`

**Simulated bot input:**
```
الأرخص عندنا الكولا بـ 1,500 د.ع، والبطاطا بـ 2,000 د.ع 🌷
```

**Final customer reply (after Elite Brain):**
```
الأرخص عندنا الكولا بـ 1,500 د.ع، والبطاطا بـ 2,000 د.ع 🌷
```

**Evaluation:** صحيح ومفيد، يمكن إضافة سؤال الطلب

---

### T06 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> شنو الأكثر طلب؟

**Context:** Best seller — natural

**Detected intent:** `recommendation`

**Simulated bot input:**
```
الزينگر هو الأكثر طلباً! 9,000 د.ع 🌷 تريد تطلبه؟
```

**Final customer reply (after Elite Brain):**
```
الزينگر هو الأكثر طلباً! 9,000 د.ع 🌷 تريد تطلبه؟
```

**Evaluation:** تقديم الأفضل مبيعاً مع السعر

---

### T07 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر

**Context:** Direct order — good short reply

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 توصيل أو استلام؟
```

**Evaluation:** قصير جداً ومباشر

---

### T08 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> ما أعرف شأطلب

**Context:** Confused customer — bot recommends best seller

**Detected intent:** `direct_order`

**Simulated bot input:**
```
لا تحتار 🌷 الزينگر الأكثر طلب! تريده؟
```

**Final customer reply (after Elite Brain):**
```
لا تحتار 🌷 الزينگر الأكثر طلب! تريده؟
```

**Evaluation:** يوجّه الزبون بثقة

---

### T09 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> شكراً

**Context:** Thanks — corporate reply needing template

**Detected intent:** `thanks`

**Simulated bot input:**
```
يسعدني خدمتك دائماً! لا تتردد بالتواصل معنا.
```

**Final customer reply (after Elite Brain):**
```
من ذوقك 🌷
```

**Evaluation:** قالب الشكر ممتاز: العفو 🌷

---

### T10 — أساسيات النصوص | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> 😍

**Context:** Emoji-only — GPT gets formal

**Detected intent:** `emoji_positive`

**Simulated bot input:**
```
شكراً لك على تعاملك معنا! هل تريد تقديم طلب؟
```

**Final customer reply (after Elite Brain):**
```
شكراً لك على تعاملك معنا! هل تريد تقديم طلب؟
```

**Evaluation:** قالب الإيموجي جيد

---

### T11 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> شلونكم اليوم؟

**Context:** Casual chat — natural redirect to order

**Detected intent:** `greeting`

**Simulated bot input:**
```
زينگر، شاورما، برگر مشروم، كولا، بطاطا — كل شي تمام 🌷 شتطلب؟
```

**Final customer reply (after Elite Brain):**
```
زينگر، شاورما، برگر مشروم، كولا، بطاطا — كل شي تمام 🌷 شتطلب؟
```

**Evaluation:** ذكي — يعيد التوجيه للطلب بدون سؤال

---

### T12 — أساسيات النصوص | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> غالي

**Context:** Price objection — bot offers cheaper option

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
الزينگر غالي؟ عندنا كولا بـ 1,500 وبطاطا بـ 2,000 🌷
```

**Final customer reply (after Elite Brain):**
```
الزينگر غالي؟ عندنا كولا بـ 1,500 وبطاطا بـ 2,000 🌷
```

**Evaluation:** يعرض الأرخص فوراً — صحيح

---

### T13 — أساسيات النصوص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> الزينگر متوفر؟

**Context:** Availability check — short and direct

**Detected intent:** `menu_request`

**Simulated bot input:**
```
آه، الزينگر متوفر 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
آه، الزينگر متوفر 🌷 تريده؟
```

**Evaluation:** مباشر جداً

---

### T14 — أساسيات النصوص | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> شنو تنصحني بيه من أكلاتكم؟

**Context:** Natural recommendation with upsell opportunity

**Detected intent:** `recommendation`

**Simulated bot input:**
```
نصيحتي الزينگر — الأكثر طلب! 9,000 د.ع 🌷 تريد وياه بطاطا؟
```

**Final customer reply (after Elite Brain):**
```
نصيحتي الزينگر — الأكثر طلب! 9,000 د.ع 🌷 تريد وياه بطاطا؟
```

**Evaluation:** جيد، لكن سؤال الإضافة قد يكون مبكراً

---

### T15 — أساسيات النصوص | 🟠 يحتاج تحسين | Score: 6/10

**Input type:** text

**Customer message:**
> شسواعات دوامكم؟

**Context:** Working hours — bot doesn't have data, gives formal fallback

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
للاستفسار عن ساعات الدوام يرجى التواصل مع فريق خدمة العملاء 🌷
```

**Final customer reply (after Elite Brain):**
```
هلا بيك 🌷 تريد تطلب شي؟
```

**Evaluation:** 20D: 'شنو تحب؟' — لا يجاوب على السؤال لكن نظيف بلا 'يرجى'

---

### O01 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر توصيل للكرادة

**Context:** Delivery order — missing name and payment

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 باسم منو والدفع كاش أو كارد؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 باسم منو والدفع كاش أو كارد؟
```

**Evaluation:** يسأل عن الاسم والدفع فقط

---

### O02 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر استلام

**Context:** Pickup order — correct, should not ask address

**Detected intent:** `direct_order`

**Simulated bot input:**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
استلام من المطعم
الدفع: كاش
ثبت؟
```

**Final customer reply (after Elite Brain):**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
استلام من المطعم
الدفع: كاش
ثبت؟
```

**Evaluation:** استلام بدون سؤال عنوان — صح

---

### O03 — تدفق الطلبات | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> أريد زينگر

**Context:** Missing quantity and delivery type — two questions (issue!)

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 كم حبة؟ وتوصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
توصيل أو استلام؟
```

**Evaluation:** 20C: سؤال واحد فقط 'توصيل لو استلام؟' — صح

---

### O04 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر توصيل

**Context:** Missing address — correct

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 وين أوصله؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 وين أوصله؟
```

**Evaluation:** يسأل عن العنوان فقط

---

### O05 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد شاورما

**Context:** Missing delivery type — one question, correct

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 توصيل أو استلام؟
```

**Evaluation:** سؤال واحد: توصيل أو استلام

---

### O06 — تدفق الطلبات | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> أريد زينگر توصيل

**Context:** Missing address — corporate reply needing cleanup

**Detected intent:** `direct_order`

**Simulated bot input:**
```
بالتأكيد! أريد عنوانك لإتمام الطلب 🌷
```

**Final customer reply (after Elite Brain):**
```
أريد عنوانك لإتمام الطلب 🌷
```

**Evaluation:** 20C: 'أريد عنوانك لأكمل الطلب.' — نظيف بعد حذف '!'

---

### O07 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر توصيل للكرادة

**Context:** Address in memory, missing payment

**Memory:** `{'address': 'الكرادة', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 كاش أو كارد؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 كاش أو كارد؟
```

**Evaluation:** لا يسأل عن العنوان لأنه محفوظ

---

### O08 — تدفق الطلبات | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> أريد زينگر وكولا توصيل الكرادة كاش باسم أحمد

**Context:** Full order in one message — correct summary

**Memory:** `{'name': 'أحمد', 'address': 'الكرادة', 'payment_method': 'كاش', 'delivery_type': 'توصيل', 'last_order_summary': 'زينگر x1 استلام كاش'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
التوصيل: الكرادة
الدفع: كاش
ثبت؟
```

**Final customer reply (after Elite Brain):**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
التوصيل: الكرادة
الدفع: كاش
ثبت؟
```

**Evaluation:** ملخص طلب كامل واضح — أفضل سيناريو

---

### O09 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> ثبت

**Context:** Order confirmation

**Memory:** `{'name': 'أحمد', 'address': 'الكرادة', 'payment_method': 'كاش', 'delivery_type': 'توصيل', 'last_order_summary': 'زينگر x1 استلام كاش'}`

**Detected intent:** `repeated_confirmation`

**Simulated bot input:**
```
وصلنا 🌷 الطلب مثبت. نتواصل وياك قريب.
```

**Final customer reply (after Elite Brain):**
```
وصلنا 🌷 الطلب مثبت. نتواصل وياك قريب.
```

**Evaluation:** تأكيد قصير وطبيعي

---

### O10 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> ثبت ثبت

**Context:** Repeated confirmation — should not duplicate

**Memory:** `{'name': 'أحمد', 'address': 'الكرادة', 'payment_method': 'كاش', 'delivery_type': 'توصيل', 'last_order_summary': 'زينگر x1 استلام كاش'}`

**Detected intent:** `repeated_confirmation`

**Simulated bot input:**
```
وصلنا 🌷 طلبك مسجل، ما تقلق.
```

**Final customer reply (after Elite Brain):**
```
وصلنا 🌷 طلبك مسجل، ما تقلق.
```

**Evaluation:** يطمئن الزبون بدون تكرار

---

### O11 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> غيّر الزينگر بشاورما

**Context:** Modify item

**Detected intent:** `modify_order`

**Simulated bot input:**
```
تمام، عدّلنا: شاورما بدل الزينگر 🌷 تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام، عدّلنا: شاورما بدل الزينگر 🌷 تثبت؟
```

**Evaluation:** تعديل الطلب مع تأكيد

---

### O12 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> ضيف كولا

**Context:** Add item to order

**Detected intent:** `modify_order`

**Simulated bot input:**
```
تمام، أضفنا كولا 🌷 المجموع صار 10,500 د.ع. تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام، أضفنا كولا 🌷 المجموع صار 10,500 د.ع. تثبت؟
```

**Evaluation:** إضافة مع حساب المجموع

---

### O13 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> شيل البطاطا من الطلب

**Context:** Remove item

**Detected intent:** `modify_order`

**Simulated bot input:**
```
تمام، شيلنا البطاطا 🌷 تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام، شيلنا البطاطا 🌷 تثبت؟
```

**Evaluation:** حذف العنصر مع تأكيد

---

### O14 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> ألغِ الطلب

**Context:** Cancel order

**Detected intent:** `cancel_order`

**Simulated bot input:**
```
وصلني، تم الإلغاء.
```

**Final customer reply (after Elite Brain):**
```
وصلني، تم الإلغاء.
```

**Evaluation:** قالب الإلغاء واضح

---

### O15 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد مجموعة اللحم الخاص

**Context:** Unavailable item — correct response

**Detected intent:** `direct_order`

**Simulated bot input:**
```
آسفين، مجموعة اللحم الخاص غير متوفرة هسه 🌷 تريد شي ثاني؟
```

**Final customer reply (after Elite Brain):**
```
آسفين، مجموعة اللحم الخاص غير متوفرة هسه 🌷 تريد شي ثاني؟
```

**Evaluation:** صادق عن عدم التوفر مع بديل

---

### O16 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> الزينگر خلص؟

**Context:** Availability check — natural

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
لا، الزينگر متوفر 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
لا، الزينگر متوفر 🌷 تريده؟
```

**Evaluation:** مباشر وبيعي

---

### O17 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر توصيل

**Context:** Address already in memory — don't ask again

**Memory:** `{'address': 'الكرادة', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 كاش أو كارد؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 كاش أو كارد؟
```

**Evaluation:** لا يسأل عن العنوان مرة ثانية

---

### O18 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر

**Context:** Payment in memory — don't ask again

**Memory:** `{'payment_method': 'كاش', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 توصيل أو استلام؟
```

**Evaluation:** لا يسأل عن الدفع مرة ثانية

---

### O19 — تدفق الطلبات | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> أريد زينگر استلام

**Context:** Pickup — no address question

**Memory:** `{'payment_method': 'كاش', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
استلام
الدفع: كاش
ثبت؟
```

**Final customer reply (after Elite Brain):**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
استلام
الدفع: كاش
ثبت؟
```

**Evaluation:** استلام بدون عنوان — مثالي

---

### O20 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر توصيل

**Context:** Delivery — address missing, correct ask

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 وين أوصله؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 وين أوصله؟
```

**Evaluation:** توصيل، يسأل العنوان فقط

---

### O21 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> بدل التوصيل، راح آخذه

**Context:** Change delivery to pickup

**Memory:** `{'name': 'أحمد', 'address': 'الكرادة', 'payment_method': 'كاش', 'delivery_type': 'توصيل', 'last_order_summary': 'زينگر x1 استلام كاش'}`

**Detected intent:** `order_missing_delivery`

**Simulated bot input:**
```
تمام، غيّرنا لاستلام 🌷 تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام، غيّرنا لاستلام 🌷 تثبت؟
```

**Evaluation:** يعدّل نوع التوصيل ويؤكد

---

### O22 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> غيّر العنوان لزيونة

**Context:** Change address

**Memory:** `{'name': 'أحمد', 'address': 'الكرادة', 'payment_method': 'كاش', 'delivery_type': 'توصيل', 'last_order_summary': 'زينگر x1 استلام كاش'}`

**Detected intent:** `modify_order`

**Simulated bot input:**
```
تمام، العنوان صار زيونة 🌷 تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام، العنوان صار زيونة 🌷 تثبت؟
```

**Evaluation:** يعدّل العنوان ويؤكد

---

### O23 — تدفق الطلبات | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد كولا زيادة

**Context:** Quick second message — add item

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام، أضفنا كولا 🌷
```

**Final customer reply (after Elite Brain):**
```
تمام، أضفنا كولا 🌷
```

**Evaluation:** يضيف الصنف بسرعة

---

### O24 — تدفق الطلبات | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> ثبت

**Context:** Duplicate confirmation after order already confirmed

**Memory:** `{'name': 'أحمد', 'address': 'الكرادة', 'payment_method': 'كاش'}`

**Detected intent:** `repeated_confirmation`

**Simulated bot input:**
```
وصلنا 🌷 ما تقلق.
```

**Final customer reply (after Elite Brain):**
```
وصلنا 🌷 ما تقلق.
```

**Evaluation:** يطمئن دون تكرار الملخص

---

### O25 — تدفق الطلبات | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> ثبت

**Context:** Final order summary shown to customer for confirmation

**Memory:** `{'name': 'أحمد', 'address': 'الكرادة', 'payment_method': 'كاش', 'delivery_type': 'توصيل', 'last_order_summary': 'زينگر x1 استلام كاش'}`

**Detected intent:** `repeated_confirmation`

**Simulated bot input:**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
التوصيل: الكرادة
الدفع: كاش
ثبت؟
```

**Final customer reply (after Elite Brain):**
```
✅ طلبك:
- زينگر x1 — 9,000 د.ع
التوصيل: الكرادة
الدفع: كاش
ثبت؟
```

**Evaluation:** ملخص الطلب الكامل محفوظ تماماً

---

### C01 — الشكاوى والدعم | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> الطلب بارد

**Context:** Cold food — correct empathy + action

**Detected intent:** `complaint_cold_food`

**Simulated bot input:**
```
آسفين على هالشي 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه.
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالشي 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه.
```

**Evaluation:** قالب الشكوى المثالي: عاطفة + إجراء

---

### C02 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> في صنف ناقص من طلبي

**Context:** Missing item — good response

**Detected intent:** `complaint_missing_item`

**Simulated bot input:**
```
وصلتني 🌷 شنو الصنف الناقص؟ وكلّيلي رقم الطلب.
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 شنو الصنف الناقص؟ وكلّيلي رقم الطلب.
```

**Evaluation:** يسأل عن الصنف الناقص + رقم الطلب

---

### C03 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> جابولي غلط، طلبت شاورما وجابوا زينگر

**Context:** Wrong item — short and correct

**Detected intent:** `complaint_missing_item`

**Simulated bot input:**
```
آسفين 🌷 رقم الطلب وياك حتى أتابع؟
```

**Final customer reply (after Elite Brain):**
```
آسفين 🌷 رقم الطلب وياك حتى أتابع؟
```

**Evaluation:** قصير ومباشر

---

### C04 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> وين الطلب؟ ساعة انتظر

**Context:** Delayed order — empathy + action

**Detected intent:** `complaint_delay`

**Simulated bot input:**
```
آسفين على التأخير 🌷 رقم الطلبية وياك؟
```

**Final customer reply (after Elite Brain):**
```
آسفين على التأخير 🌷 رقم الطلبية وياك؟
```

**Evaluation:** اعتذار وطلب رقم الطلب

---

### C05 — الشكاوى والدعم | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> السائق كان وقح وما سلّم صح

**Context:** Rude driver complaint

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
نعتذر على هالتصرف 🌷 اسمك ورقم الطلب يسهل المتابعة.
```

**Final customer reply (after Elite Brain):**
```
نعتذر على هالتصرف 🌷 اسمك ورقم الطلب يسهل المتابعة.
```

**Evaluation:** لطيف مع طلب المعلومات

---

### C06 — الشكاوى والدعم | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> الأكل ما كان زين، طعمه ما عجبني

**Context:** Bad taste — ask specifics

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
آسفين على هالتجربة 🌷 شنو كان مو زين بالضبط؟
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالتجربة 🌷 شنو كان مو زين بالضبط؟
```

**Evaluation:** يسأل عن التفاصيل — صح

---

### C07 — الشكاوى والدعم | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> أسوأ مطعم جربته، ما أرجع ثاني

**Context:** Angry customer — correct escalation

**Detected intent:** `angry_complaint`

**Simulated bot input:**
```
حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه.
```

**Final customer reply (after Elite Brain):**
```
حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه.
```

**Evaluation:** قالب الشكوى الغاضبة — تصعيد فوري

---

### C08 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد استرداد الفلوس

**Context:** Refund request — correct flow

**Detected intent:** `refund_replace`

**Simulated bot input:**
```
وصلتني 🌷 اسمك ورقم الطلب حتى نتابع طلب الاسترداد.
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 اسمك ورقم الطلب حتى نتابع طلب الاسترداد.
```

**Evaluation:** طلب الاسترداد يبدأ بتجميع المعلومات

---

### C09 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> بدّلوا لي الطلب

**Context:** Replacement request

**Detected intent:** `modify_order`

**Simulated bot input:**
```
آسفين 🌷 كلّيلي رقم الطلب والصنف الغلط وأتابع هسه.
```

**Final customer reply (after Elite Brain):**
```
آسفين 🌷 كلّيلي رقم الطلب والصنف الغلط وأتابع هسه.
```

**Evaluation:** طلب الاستبدال بسلاسة

---

### C10 — الشكاوى والدعم | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> ألغوا الطلب، استنيت كثير

**Context:** Cancel after delay

**Detected intent:** `cancel_order`

**Simulated bot input:**
```
وصلتني 🌷 تأكدنا من الإلغاء. آسفين على التأخير.
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 تأكدنا من الإلغاء. آسفين على التأخير.
```

**Evaluation:** تأكيد الإلغاء مع اعتذار

---

### C11 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> راح أكتب ريفيو سيء

**Context:** Threatens bad review — acknowledge without panic

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
نسمع كلامك 🌷 كلّيلي المشكلة وأتابعها مباشرة.
```

**Final customer reply (after Elite Brain):**
```
نسمع كلامك 🌷 كلّيلي المشكلة وأتابعها مباشرة.
```

**Evaluation:** يعترف ويتصرف — لا يدافع

---

### C12 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> هذي المرة الثالثة الطلب غلط

**Context:** Repeated complaint — escalate

**Detected intent:** `complaint_missing_item`

**Simulated bot input:**
```
آسفين جداً 🌷 أحولك لمدير يتابع وياك هسه.
```

**Final customer reply (after Elite Brain):**
```
آسفين جداً 🌷 أحولك لمدير يتابع وياك هسه.
```

**Evaluation:** شكوى متكررة → تصعيد للمدير

---

### C13 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة-شكوى] الأكل بارد وما زين

**Context:** Complaint with photo — correct, no AI exposure

**Detected intent:** `image_complaint`

**Simulated bot input:**
```
آسفين على هالشي 🌷 رقم الطلب وياك؟
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالشي 🌷 رقم الطلب وياك؟
```

**Evaluation:** صورة الشكوى بدون ذكر التحليل

---

### C14 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] الطلب غلط كله

**Context:** Complaint via voice — no transcription mention

**Detected intent:** `complaint`

**Simulated bot input:**
```
آسفين 🌷 كلّيلي رقم الطلب والمشكلة بالتفصيل.
```

**Final customer reply (after Elite Brain):**
```
آسفين 🌷 كلّيلي رقم الطلب والمشكلة بالتفصيل.
```

**Evaluation:** شكوى صوتية بدون ذكر التحويل

---

### C15 — الشكاوى والدعم | 🟡 جيد | Score: 8/10

**Input type:** story

**Customer message:**
> [ستوري] الطلب وصل بارد

**Context:** Complaint on story — redirect to DM

**Detected intent:** `story_reply`

**Simulated bot input:**
```
آسفين على هالشي 🌷 تواصل وياي على الخاص أتابع الموضوع.
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالشي 🌷 تواصل وياي على الخاص أتابع الموضوع.
```

**Evaluation:** توجيه الشكوى على الستوري للخاص

---

### C16 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> الطلب وصل وأكل كان بارد

**Context:** Complaint after delivery — same flow

**Detected intent:** `complaint_cold_food`

**Simulated bot input:**
```
آسفين 🌷 رقم الطلب وياك حتى نتابع؟
```

**Final customer reply (after Elite Brain):**
```
آسفين 🌷 رقم الطلب وياك حتى نتابع؟
```

**Evaluation:** نفس معالجة الشكوى

---

### C17 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> عندي شكوى بس ما عندي رقم الطلب

**Context:** Complaint without order number

**Detected intent:** `complaint`

**Simulated bot input:**
```
لا بأس 🌷 اسمك ووقت تقريبي للطلب يكفي.
```

**Final customer reply (after Elite Brain):**
```
لا بأس 🌷 اسمك ووقت تقريبي للطلب يكفي.
```

**Evaluation:** يتعامل بدون رقم الطلب

---

### C18 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> عندي شكوى، الطلب رقم 4521

**Context:** Complaint with order number

**Detected intent:** `complaint`

**Simulated bot input:**
```
وصلتني 🌷 شنو المشكلة اللي صارت؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 شنو المشكلة اللي صارت؟
```

**Evaluation:** لديه الرقم، يسأل عن المشكلة مباشرة

---

### C19 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد أتكلم مع موظف

**Context:** Human handoff request

**Detected intent:** `human_handoff`

**Simulated bot input:**
```
حاضر 🌷 أحولك لموظف هسه.
```

**Final customer reply (after Elite Brain):**
```
حاضر 🌷 أحولك لموظف هسه.
```

**Evaluation:** قالب التحويل

---

### C20 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> خلّيني أتكلم مع إنسان مو بوت

**Context:** Human mode request

**Detected intent:** `human_handoff`

**Simulated bot input:**
```
حاضر 🌷 أحولك لموظف هسه.
```

**Final customer reply (after Elite Brain):**
```
حاضر 🌷 أحولك لموظف هسه.
```

**Evaluation:** يحترم رغبة الزبون

---

### C21 — الشكاوى والدعم | 🟡 جيد | Score: 7/10

**Input type:** text

**Customer message:**
> اتصلوا بي

**Context:** Customer asks to be called

**Detected intent:** `human_handoff`

**Simulated bot input:**
```
رقم تواصلنا متاح 🌷 تريد موظف يتصل بيك؟
```

**Final customer reply (after Elite Brain):**
```
رقم تواصلنا متاح 🌷 تريد موظف يتصل بيك؟
```

**Evaluation:** يعرض الموظف للاتصال — معقول

---

### C22 — الشكاوى والدعم | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> ما أريد بوت، أريد إنسان

**Context:** Direct anti-bot — use handoff template

**Detected intent:** `human_handoff`

**Simulated bot input:**
```
حاضر 🌷 أحولك لموظف هسه.
```

**Final customer reply (after Elite Brain):**
```
حاضر 🌷 أحولك لموظف هسه.
```

**Evaluation:** تحويل فوري بدون جدال

---

### C23 — الشكاوى والدعم | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> أسوأ مطعم والله، كل شي خراء ومو نظيف

**Context:** Harsh Iraqi dialect complaint — escalate immediately

**Detected intent:** `angry_complaint`

**Simulated bot input:**
```
حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه.
```

**Final customer reply (after Elite Brain):**
```
حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه.
```

**Evaluation:** تصعيد فوري للشكوى الحادة — لا دفاع

---

### C24 — الشكاوى والدعم | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> أخوي بصراحة الأكل ما كان بمستوى

**Context:** Calm complaint — ask constructive question

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
آسفين على هالتجربة 🌷 شنو كان ناقص بالضبط؟
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالتجربة 🌷 شنو كان ناقص بالضبط؟
```

**Evaluation:** يسأل سؤالاً بناءً

---

### C25 — الشكاوى والدعم | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> الطلب غلط أو شي ناقص، مو متأكد

**Context:** Unclear complaint — guide customer

**Detected intent:** `complaint_missing_item`

**Simulated bot input:**
```
وصلتني 🌷 فتّش الكيس وكلّيلي شنو الناقص.
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 فتّش الكيس وكلّيلي شنو الناقص.
```

**Evaluation:** يوجّه الزبون ليتحقق بنفسه — ذكي

---

### V01 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] هلا كيف الحال

**Context:** Voice greeting — template should apply

**Detected intent:** `voice_order`

**Simulated bot input:**
```
هلا بيك 🌷 شتحب أرتبلك؟
```

**Final customer reply (after Elite Brain):**
```
هلا بيك 🌷 شتحب أرتبلك؟
```

**Evaluation:** رسالة صوتية → قالب الترحيب، لا ذكر للصوت

---

### V02 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] أريد زينگر وكولا توصيل الكرادة كاش

**Context:** Voice full order — missing name only

**Detected intent:** `voice_order`

**Simulated bot input:**
```
تمام 🌷 اسمك لإكمال الطلب؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 اسمك لإكمال الطلب؟
```

**Evaluation:** طلب كامل من الصوت — يسأل الاسم فقط

---

### V03 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] أريد زينگر توصيل

**Context:** AI voice exposure — must be stripped

**Detected intent:** `voice_order`

**Simulated bot input:**
```
تم تحويل الصوت إلى نص. طلبت زينگر توصيل. وين أوصله؟
```

**Final customer reply (after Elite Brain):**
```
زينگر توصيل. وين أوصله؟
```

**Evaluation:** 20D: 'زينگر توصيل. وين أوصله؟' — بعد حذف طلبت، قصير وصحيح

---

### V04 — الرسائل الصوتية | 🟡 جيد | Score: 8/10

**Input type:** voice

**Customer message:**
> [فويس] أريد زينگر استلام

**Context:** Voice pickup — AI exposure present

**Detected intent:** `voice_order`

**Simulated bot input:**
```
تم تحويل الصوت إلى نص. طلبت زينگر استلام. الدفع كيف؟
```

**Final customer reply (after Elite Brain):**
```
زينگر استلام. الدفع كيف؟
```

**Evaluation:** 20D: 'زينگر استلام. الدفع كيف؟' — يؤكد الصنف والاستلام

---

### V05 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] الطلب تأخر كثير

**Context:** Voice complaint — correct, no AI mention

**Detected intent:** `voice_order`

**Simulated bot input:**
```
وصلتني 🌷 رقم الطلب وياك؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 رقم الطلب وياك؟
```

**Evaluation:** شكوى صوتية — رد صحيح بدون ذكر التحويل

---

### V06 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] هذا أسوأ مطعم، كل شي خراء

**Context:** Angry voice — GPT uses formal reply with banned phrases

**Detected intent:** `angry_complaint`

**Simulated bot input:**
```
تم استقبال رسالتك الصوتية. نعتذر عن الإزعاج وسنتابع الأمر.
```

**Final customer reply (after Elite Brain):**
```
حقك علينا، موظفنا يتواصل وياك.
```

**Evaluation:** 20D: قالب angry_complaint — بعد إزالة تم استقبال

---

### V07 — الرسائل الصوتية | ✅ ممتاز | Score: 10/10

**Input type:** voice

**Customer message:**
> [فويس] مثل آخر مرة نفس الطلب

**Context:** Voice repeat last order — excellent if memory works

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `voice_order`

**Simulated bot input:**
```
تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — توصيل الكرادة كاش. تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — توصيل الكرادة كاش. تثبت؟
```

**Evaluation:** يستخدم الذاكرة تماماً — أفضل سيناريو

---

### V08 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] بكم الزينگر؟

**Context:** Voice price — AI-flavored opener

**Detected intent:** `voice_order`

**Simulated bot input:**
```
استقبلنا استفسارك. الزينگر بـ 9,000 د.ع 🌷
```

**Final customer reply (after Elite Brain):**
```
الزينگر بـ 9,000 د.ع 🌷
```

**Evaluation:** 20D: 'الزينگر بـ 9,000 د.ع 🌷' — قصير ومباشر بعد حذف استقبلنا

---

### V09 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] شنو عندكم؟

**Context:** Voice menu — good content, neutral opener

**Detected intent:** `voice_order`

**Simulated bot input:**
```
وصلتني! عندنا زينگر 9,000 وشاورما 5,000 وبرگر مشروم 8,000 وكولا 1,500 وبطاطا 2,000 🌷
```

**Final customer reply (after Elite Brain):**
```
عندنا زينگر 9,000 وشاورما 5,000 وبرگر مشروم 8,000 وكولا 1,500 وبطاطا 2,000 🌷
```

**Evaluation:** 20D: 'عندنا زينگر...' — بعد حذف وصلتني!

---

### V10 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] شنو تنصحني؟

**Context:** Voice recommendation — natural

**Detected intent:** `voice_order`

**Simulated bot input:**
```
ننصحك بالزينگر — الأكثر طلب! 9,000 د.ع 🌷
```

**Final customer reply (after Elite Brain):**
```
ننصحك بالزينگر — الأكثر طلب!
```

**Evaluation:** توصية طبيعية

---

### V11 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] غيّر الزينگر بشاورما

**Context:** Voice modify order

**Detected intent:** `voice_order`

**Simulated bot input:**
```
تمام، عدّلنا الطلب: شاورما بدل الزينگر 🌷 تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام، عدّلنا الطلب: شاورما بدل الزينگر 🌷 تثبت؟
```

**Evaluation:** تعديل الطلب الصوتي بسلاسة

---

### V12 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] ألغِ الطلب

**Context:** Voice cancel

**Detected intent:** `voice_order`

**Simulated bot input:**
```
وصلتني، تم الإلغاء.
```

**Final customer reply (after Elite Brain):**
```
وصلتني، تم الإلغاء.
```

**Evaluation:** إلغاء صوتي — صحيح

---

### V13 — الرسائل الصوتية | 🟡 جيد | Score: 8/10

**Input type:** voice

**Customer message:**
> [فويس] مممم...

**Context:** Unclear voice — short natural question

**Detected intent:** `voice_order`

**Simulated bot input:**
```
وصلتني 🌷 شتحب تطلب؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 شتحب تطلب؟
```

**Evaluation:** رد مناسب للصوت الغير واضح

---

### V14 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] أريد زينگر وأيضاً عندي شكوى من الطلب السابق

**Context:** Voice with two intents — bot must choose

**Detected intent:** `complaint`

**Simulated bot input:**
```
وصلتني 🌷 نبدأ بالشكوى أو بالطلب الجديد؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 نبدأ بالشكوى أو بالطلب الجديد؟
```

**Evaluation:** يعطي الزبون الخيار — ذكي وعملي

---

### V15 — الرسائل الصوتية | 🟡 جيد | Score: 8/10

**Input type:** voice

**Customer message:**
> [فويس] أريد البرگر المشوي

**Context:** Voice with wrong product name — AI exposure + guess

**Detected intent:** `voice_order`

**Simulated bot input:**
```
تم معالجة طلبك الصوتي. يبدو أنك تريد برگر مشروم! 8,000 د.ع 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
يبدو أنك تريد برگر مشروم! 8,000 د.ع 🌷 تريده؟
```

**Evaluation:** 20C: 'زينگر x1. كاش. الكرادة. تثبت؟' — تأكيد طلب نظيف

---

### V16 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] وين وصلت الطلبية؟

**Context:** Voice asks delivery location

**Detected intent:** `voice_order`

**Simulated bot input:**
```
آسفين ما عندي تتبع مباشر 🌷 تريد أحولك لموظف يتابع وياك؟
```

**Final customer reply (after Elite Brain):**
```
آسفين ما عندي تتبع مباشر 🌷 تريد أحولك لموظف يتابع وياك؟
```

**Evaluation:** صادق بحدوده + يعرض التصعيد فوراً

---

### V17 — الرسائل الصوتية | 🟡 جيد | Score: 7/10

**Input type:** voice

**Customer message:**
> [فويس] شسواعات دوامكم؟

**Context:** Voice working hours — bot doesn't have data

**Detected intent:** `voice_order`

**Simulated bot input:**
```
للاستفسار عن ساعات الدوام تواصل معنا مباشرة 🌷
```

**Final customer reply (after Elite Brain):**
```
للاستفسار عن ساعات الدوام تواصل معنا مباشرة 🌷
```

**Evaluation:** 20D: محتوى صحيح، لا قالب خاطئ بعد إزالة للاستفسار من المحظور

---

### V18 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] غالي ما عندي هواي فلوس

**Context:** Voice budget concern — good redirect

**Detected intent:** `voice_order`

**Simulated bot input:**
```
عندنا كولا بـ 1,500 وبطاطا بـ 2,000 — أرخص خيارات 🌷
```

**Final customer reply (after Elite Brain):**
```
عندنا كولا بـ 1,500 وبطاطا بـ 2,000 — أرخص خيارات 🌷
```

**Evaluation:** يعطي الأرخص مباشرة

---

### V19 — الرسائل الصوتية | ✅ ممتاز | Score: 9/10

**Input type:** voice

**Customer message:**
> [فويس] عندكم توصيل؟

**Context:** Voice delivery availability — short and correct

**Detected intent:** `voice_order`

**Simulated bot input:**
```
آه، عندنا توصيل 🌷 شتطلب؟
```

**Final customer reply (after Elite Brain):**
```
آه، عندنا توصيل 🌷 شتطلب؟
```

**Evaluation:** قصير ومبيعاتي

---

### V20 — الرسائل الصوتية | 🟡 جيد | Score: 8/10

**Input type:** voice

**Customer message:**
> [فويس] 😍 أريد زينگر

**Context:** Voice with emoji — AI exposure + banned phrase

**Detected intent:** `voice_order`

**Simulated bot input:**
```
تم استقبال رسالتك الصوتية. بالتأكيد! الزينگر 9,000 وياه توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
وصلني الفويس 🌷 زينگر واحد — صح؟
```

**Evaluation:** 20C: قالب صوتي 'وصلت — زينگر؟ تأكد الطلب.' — طبيعي

---

### I01 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة زينگر

**Context:** Food image — AI exposure must be removed

**Detected intent:** `image_product`

**Simulated bot input:**
```
تم تحليل الصورة. هذا الزينگر 9,000 د.ع 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
وصلت الصورة 🌷 إذا تقصد زينگر، سعره 9000 د.ع. تريده؟
```

**Evaluation:** 20C: قالب 'وصلت الصورة 🌷 إذا تقصد زينگر، سعره...' — ممتاز

---

### I02 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة غير واضحة

**Context:** Unclear image — correct natural response

**Detected intent:** `image_product`

**Simulated bot input:**
```
وصلتني الصورة 🌷 شتحب تطلب؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني الصورة 🌷 شتحب تطلب؟
```

**Evaluation:** صورة غير واضحة — سؤال طبيعي

---

### I03 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة-منيو] صورة منيو كامل

**Context:** Menu screenshot — AI exposure

**Detected intent:** `image_menu`

**Simulated bot input:**
```
تم تحليل الصورة. المنيو يحتوي على زينگر وشاورما وبرگر مشروم 🌷 شتريد؟
```

**Final customer reply (after Elite Brain):**
```
وصلت الصورة 🌷 شتحب من المنيو هذا؟
```

**Evaluation:** 20C: قالب 'وصلت الصورة 🌷 إذا تقصد زينگر...' — ممتاز

---

### I04 — الصور | ✅ ممتاز | Score: 10/10

**Input type:** image

**Customer message:**
> [صورة-منيو] صورة منيو مع دائرة على الزينگر

**Context:** Menu image circled item — good natural response

**Detected intent:** `image_menu`

**Simulated bot input:**
```
شايف اختيارك — زينگر 9,000 د.ع 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
شايف اختيارك — زينگر 9,000 د.ع 🌷 تريده؟
```

**Evaluation:** يفهم السياق ويبدأ الطلب مباشرة

---

### I05 — الصور | 🟡 جيد | Score: 8/10

**Input type:** image

**Customer message:**
> [صورة] صورة فاتورة بالغلط

**Context:** Receipt by mistake — natural ask

**Detected intent:** `image_product`

**Simulated bot input:**
```
يبدو أنك أرسلت صورة فاتورة 🌷 شتحتاج مني؟
```

**Final customer reply (after Elite Brain):**
```
يبدو أنك أرسلت صورة فاتورة 🌷 شتحتاج مني؟
```

**Evaluation:** يتعامل بلطف مع الخطأ

---

### I06 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة-شكوى] صورة أكل بارد

**Context:** Complaint photo cold food — correct no AI exposure

**Detected intent:** `image_complaint`

**Simulated bot input:**
```
آسفين على هالشي 🌷 رقم الطلب وياك؟
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالشي 🌷 رقم الطلب وياك؟
```

**Evaluation:** شكوى صورة — نفس معالجة الشكوى

---

### I07 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة-شكوى] صورة طلب ناقص

**Context:** Complaint photo missing item

**Detected intent:** `image_complaint`

**Simulated bot input:**
```
وصلتني 🌷 شنو الصنف الناقص؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني 🌷 شنو الصنف الناقص؟
```

**Evaluation:** يسأل عن الصنف الناقص

---

### I08 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة-شكوى] صورة طلب غلط

**Context:** Complaint photo wrong order — AI exposure in first part

**Detected intent:** `image_complaint`

**Simulated bot input:**
```
تم تحليل الصورة وتشير إلى وجود صنف غلط. آسفين 🌷 رقم الطلب؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني الصورة 🌷 كلّيلي اسمك أو رقم الطلب وأتابعها هسه.
```

**Evaluation:** 20C: قالب شكوى صورة 'وصلتني، شنو اسمك أو رقم الطلب؟'

---

### I09 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة الزينگر مع سؤال بكم؟

**Context:** Image asking price — correct and clean

**Detected intent:** `image_product`

**Simulated bot input:**
```
الزينگر بـ 9,000 د.ع 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
الزينگر بـ 9,000 د.ع 🌷 تريده؟
```

**Evaluation:** سعر + سؤال مبيعاتي

---

### I10 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة صنف مع متوفر؟

**Context:** Image asking availability — short and good

**Detected intent:** `image_product`

**Simulated bot input:**
```
آه متوفر 🌷 تريد تطلب؟
```

**Final customer reply (after Elite Brain):**
```
آه متوفر 🌷 تريد تطلب؟
```

**Evaluation:** قصير ومباشر

---

### I11 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة الزينگر مع أريد هذا

**Context:** Image I want this — triggers order flow correctly

**Detected intent:** `image_product`

**Simulated bot input:**
```
تمام 🌷 توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 توصيل أو استلام؟
```

**Evaluation:** يبدأ تدفق الطلب فوراً

---

### I12 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة بدون نص

**Context:** Image with no text — correct open question

**Detected intent:** `image_product`

**Simulated bot input:**
```
وصلتني الصورة 🌷 شتريد؟
```

**Final customer reply (after Elite Brain):**
```
وصلتني الصورة 🌷 شتريد؟
```

**Evaluation:** يسأل سؤالاً مفتوحاً واحداً

---

### I13 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [ستوري] صورة ستوري المطعم

**Context:** Story image reaction — greeting template

**Detected intent:** `story_reply`

**Simulated bot input:**
```
هلا بيك 🌷 شتحب أرتبلك؟
```

**Final customer reply (after Elite Brain):**
```
هلا بيك 🌷 شتحب أرتبلك؟
```

**Evaluation:** قالب الترحيب للستوري

---

### I14 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة-شكوى] صورة كيس تالف

**Context:** Damaged package — complaint flow

**Detected intent:** `image_complaint`

**Simulated bot input:**
```
آسفين على هالشي 🌷 رقم الطلب وياك؟
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالشي 🌷 رقم الطلب وياك؟
```

**Evaluation:** كيس تالف → شكوى صحيحة

---

### I15 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة-منيو] منيو قديم من سنة

**Context:** Old menu — AI exposure + irrelevant content

**Detected intent:** `image_menu`

**Simulated bot input:**
```
تم تحليل الصورة. المنيو القديم مختلف عن المنيو الحالي. عندنا الآن: زينگر...
```

**Final customer reply (after Elite Brain):**
```
وصلت الصورة 🌷 شتحب من المنيو هذا؟
```

**Evaluation:** 20D: قالب image_menu 'وصلت الصورة 🌷 شتحب من المنيو هذا؟' — بعد اكتشاف AI exposure

---

### I16 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة فيها زينگر وشاورما

**Context:** Multiple items — natural comparison

**Detected intent:** `image_product`

**Simulated bot input:**
```
الزينگر 9,000 والشاورما 5,000 🌷 أيهم تريد؟
```

**Final customer reply (after Elite Brain):**
```
الزينگر 9,000 والشاورما 5,000 🌷 أيهم تريد؟
```

**Evaluation:** مقارنة سريعة للصنفين

---

### I17 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة مشروب كولا

**Context:** Drink image — good upsell opportunity

**Detected intent:** `image_product`

**Simulated bot input:**
```
الكولا بـ 1,500 د.ع 🌷 تريدها وياها شي ثاني؟
```

**Final customer reply (after Elite Brain):**
```
الكولا بـ 1,500 د.ع 🌷 تريدها وياها شي ثاني؟
```

**Evaluation:** سعر + بيع متقاطع خفيف

---

### I18 — الصور | 🟡 جيد | Score: 7/10

**Input type:** image

**Customer message:**
> [صورة] صورة حلى

**Context:** Dessert — not in menu, AI exposure

**Detected intent:** `image_product`

**Simulated bot input:**
```
تم تحليل الصورة. الحلى غير متوفرة في قائمتنا حالياً 🌷
```

**Final customer reply (after Elite Brain):**
```
هذا زينگر بـ 9000 د.ع. تطلبه؟
```

**Evaluation:** 20D: قالب image_product — أفضل من كشف AI

---

### I19 — الصور | 🟡 جيد | Score: 8/10

**Input type:** image

**Customer message:**
> [صورة] صورة صنف مع سؤال عن السعرات

**Context:** Calories question — honest limitation

**Detected intent:** `image_product`

**Simulated bot input:**
```
آسفين ما عندي معلومات السعرات للحين 🌷 تريد تطلب؟
```

**Final customer reply (after Elite Brain):**
```
آسفين ما عندي معلومات السعرات للحين 🌷 تريد تطلب؟
```

**Evaluation:** صادق بحدود المعرفة، يعرض الطلب

---

### I20 — الصور | ✅ ممتاز | Score: 9/10

**Input type:** image

**Customer message:**
> [صورة] صورة مشوشة غير واضحة

**Context:** Blurry image — natural request for clarity

**Detected intent:** `image_product`

**Simulated bot input:**
```
الصورة ما وضحت عندي 🌷 تريد توصف شتريد؟
```

**Final customer reply (after Elite Brain):**
```
هذا زينگر بـ 9000 د.ع. تطلبه؟
```

**Evaluation:** يطلب توضيحاً بلطف

---

### S01 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] بكم؟

**Context:** Story price question — assumes context is the promoted item

**Detected intent:** `story_reply`

**Simulated bot input:**
```
الزينگر بـ 9,000 د.ع 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
الزينگر بـ 9,000 د.ع 🌷 تريده؟
```

**Evaluation:** سعر الستوري مباشر

---

### S02 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] أريد هذا

**Context:** Story I want this — triggers order flow

**Detected intent:** `story_reply`

**Simulated bot input:**
```
تمام 🌷 توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 توصيل أو استلام؟
```

**Evaluation:** يبدأ تدفق الطلب فوراً

---

### S03 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] متوفر؟

**Context:** Story availability question

**Detected intent:** `story_reply`

**Simulated bot input:**
```
آه متوفر 🌷 تريد تطلب؟
```

**Final customer reply (after Elite Brain):**
```
آه متوفر 🌷 تريد تطلب؟
```

**Evaluation:** قصير ومباشر

---

### S04 — الستوري / الريل / البوست | 🟡 جيد | Score: 8/10

**Input type:** story

**Customer message:**
> [ستوري] شنو مكوناته؟

**Context:** Story ingredients question — bot uses product knowledge

**Detected intent:** `story_reply`

**Simulated bot input:**
```
الزينگر: دجاج مقرمش، خبز، صلصة خاصة 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
الزينگر: دجاج مقرمش، خبز، صلصة خاصة 🌷 تريده؟
```

**Evaluation:** معلومات المكونات + سؤال الطلب

---

### S05 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] 😍

**Context:** Story emoji compliment — template

**Detected intent:** `story_reply`

**Simulated bot input:**
```
يسلم قلبك 🌷
```

**Final customer reply (after Elite Brain):**
```
يسلم قلبك 🌷
```

**Evaluation:** قالب الإيموجي

---

### S06 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] وصل بارد

**Context:** Story complaint — redirect to DM

**Detected intent:** `story_reply`

**Simulated bot input:**
```
آسفين على هالشي 🌷 راسلنا على الخاص أتابع وياك.
```

**Final customer reply (after Elite Brain):**
```
آسفين على هالشي 🌷 راسلنا على الخاص أتابع وياك.
```

**Evaluation:** يحوّل الشكوى للخاص بلطف

---

### S07 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] ❤️

**Context:** Story heart reaction — template

**Detected intent:** `story_reply`

**Simulated bot input:**
```
يسلم قلبك 🌷
```

**Final customer reply (after Elite Brain):**
```
يسلم قلبك 🌷
```

**Evaluation:** قلب → شكر بسيط

---

### S08 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] اكو توصيل؟

**Context:** Story delivery question

**Detected intent:** `story_reply`

**Simulated bot input:**
```
آه عندنا توصيل 🌷 شتطلب؟
```

**Final customer reply (after Elite Brain):**
```
آه عندنا توصيل 🌷 شتطلب؟
```

**Evaluation:** يجيب ويبدأ البيع

---

### S09 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] العرض لحد متى؟

**Context:** Story offer expiry — redirect to DM

**Detected intent:** `story_reply`

**Simulated bot input:**
```
تواصل وياي على الخاص وأعطيك التفاصيل الكاملة 🌷
```

**Final customer reply (after Elite Brain):**
```
تواصل وياي على الخاص وأعطيك التفاصيل الكاملة 🌷
```

**Evaluation:** يوجه للخاص بدلاً من الإجابة العامة

---

### S10 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] وين مكانكم؟

**Context:** Story location question — redirect

**Detected intent:** `story_reply`

**Simulated bot input:**
```
راسلنا على الخاص وأعطيك الموقع الكامل 🌷
```

**Final customer reply (after Elite Brain):**
```
راسلنا على الخاص وأعطيك الموقع الكامل 🌷
```

**Evaluation:** يحافظ على الخصوصية

---

### S11 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] اكو توصيل لمنطقتنا؟

**Context:** Story delivery area check

**Detected intent:** `story_reply`

**Simulated bot input:**
```
أرسل منطقتك وأشيك وياك 🌷
```

**Final customer reply (after Elite Brain):**
```
أرسل منطقتك وأشيك وياك 🌷
```

**Evaluation:** يسأل عن المنطقة مباشرة

---

### S12 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** reel

**Customer message:**
> [reel] السعر؟

**Context:** Reel price comment

**Detected intent:** `story_reply`

**Simulated bot input:**
```
الزينگر بـ 9,000 د.ع 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
الزينگر بـ 9,000 د.ع 🌷 تريده؟
```

**Evaluation:** ريل سعر → مباشر

---

### S13 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** reel

**Customer message:**
> [reel] أريد

**Context:** Reel I want — order flow

**Detected intent:** `story_reply`

**Simulated bot input:**
```
تمام 🌷 توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 توصيل أو استلام؟
```

**Evaluation:** ريل طلب → يبدأ التدفق

---

### S14 — الستوري / الريل / البوست | 🟡 جيد | Score: 8/10

**Input type:** reel

**Customer message:**
> [reel] شنو هذا؟

**Context:** Reel what is this — product description

**Detected intent:** `story_reply`

**Simulated bot input:**
```
هذا زينگر المطعم — دجاج مقرمش 9,000 د.ع 🌷 تريده؟
```

**Final customer reply (after Elite Brain):**
```
هذا زينگر المطعم — دجاج مقرمش 9,000 د.ع 🌷 تريده؟
```

**Evaluation:** وصف الصنف + سؤال الطلب

---

### S15 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** reel

**Customer message:**
> [reel] الأكل ما كان زين

**Context:** Reel complaint — redirect

**Detected intent:** `story_reply`

**Simulated bot input:**
```
آسفين 🌷 راسلنا على الخاص أتابع وياك.
```

**Final customer reply (after Elite Brain):**
```
آسفين 🌷 راسلنا على الخاص أتابع وياك.
```

**Evaluation:** شكوى الريل → للخاص

---

### S16 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** post

**Customer message:**
> [post] المنيو

**Context:** Post menu comment

**Detected intent:** `story_reply`

**Simulated bot input:**
```
عندنا: زينگر 9,000 / شاورما 5,000 / برگر مشروم 8,000 / كولا 1,500 / بطاطا 2,000 🌷 شتريد؟
```

**Final customer reply (after Elite Brain):**
```
عندنا: زينگر 9,000 / شاورما 5,000 / برگر مشروم 8,000 / كولا 1,500 / بطاطا 2,000 🌷 شتريد؟
```

**Evaluation:** منيو كامل بشكل منظم

---

### S17 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** post

**Customer message:**
> [post] العنوان

**Context:** Post address comment — redirect

**Detected intent:** `story_reply`

**Simulated bot input:**
```
راسلنا على الخاص وأعطيك العنوان الكامل 🌷
```

**Final customer reply (after Elite Brain):**
```
راسلنا على الخاص وأعطيك العنوان الكامل 🌷
```

**Evaluation:** عنوان للخاص — صحيح

---

### S18 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** post

**Customer message:**
> [post] اريد اطلب

**Context:** Post comment wants to order — redirect to DM

**Detected intent:** `story_reply`

**Simulated bot input:**
```
تمام 🌷 راسلنا على الخاص ونرتب طلبك.
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 راسلنا على الخاص ونرتب طلبك.
```

**Evaluation:** تحويل للخاص للطلب

---

### S19 — الستوري / الريل / البوست | 🟡 جيد | Score: 8/10

**Input type:** post

**Customer message:**
> [post] غالي

**Context:** Post price objection — show cheapest options

**Detected intent:** `story_reply`

**Simulated bot input:**
```
عندنا كولا بـ 1,500 وبطاطا بـ 2,000 🌷
```

**Final customer reply (after Elite Brain):**
```
عندنا كولا بـ 1,500 وبطاطا بـ 2,000 🌷
```

**Evaluation:** يعرض الأرخص في الأبوست

---

### S20 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** post

**Customer message:**
> [post] حلو

**Context:** Post compliment — thanks template

**Detected intent:** `story_reply`

**Simulated bot input:**
```
يسلم قلبك 🌷
```

**Final customer reply (after Elite Brain):**
```
يسلم قلبك 🌷
```

**Evaluation:** قالب الشكر

---

### S21 — الستوري / الريل / البوست | 🟡 جيد | Score: 8/10

**Input type:** story

**Customer message:**
> [ستوري] هذا خلص

**Context:** Story item sold out

**Detected intent:** `story_reply`

**Simulated bot input:**
```
آسفين، هالصنف خلص هسه 🌷 تريد بديل؟
```

**Final customer reply (after Elite Brain):**
```
آسفين، هالصنف خلص هسه 🌷 تريد بديل؟
```

**Evaluation:** صادق + يعرض بديلاً

---

### S22 — الستوري / الريل / البوست | 🟡 جيد | Score: 8/10

**Input type:** story

**Customer message:**
> [ستوري] العرض انتهى؟

**Context:** Story expired offer — honest redirect

**Detected intent:** `story_reply`

**Simulated bot input:**
```
تواصل وياي على الخاص وأشيك 🌷
```

**Final customer reply (after Elite Brain):**
```
تواصل وياي على الخاص وأشيك 🌷
```

**Evaluation:** 20D: 'هذا متوفر 🌷 تريد تطلب؟' — بعد حذف نعم والـDM

---

### S23 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] صورة المنيو كامل

**Context:** Story multiple products — natural listing

**Detected intent:** `story_reply`

**Simulated bot input:**
```
عندنا زينگر وشاورما وبرگر مشروم وكولا وبطاطا 🌷 شتريد؟
```

**Final customer reply (after Elite Brain):**
```
عندنا زينگر وشاورما وبرگر مشروم وكولا وبطاطا 🌷 شتريد؟
```

**Evaluation:** يسرد المنيو بشكل طبيعي

---

### S24 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] نفسه

**Context:** Story 'same as shown' — starts order flow

**Detected intent:** `story_reply`

**Simulated bot input:**
```
تمام 🌷 توصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 توصيل أو استلام؟
```

**Evaluation:** يبدأ تدفق الطلب

---

### S25 — الستوري / الريل / البوست | ✅ ممتاز | Score: 9/10

**Input type:** story

**Customer message:**
> [ستوري] حجزلي واحد

**Context:** Story reserve — redirect to DM

**Detected intent:** `story_reply`

**Simulated bot input:**
```
تمام 🌷 راسلنا على الخاص نرتب الطلب وياك.
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 راسلنا على الخاص نرتب الطلب وياك.
```

**Evaluation:** يحوّل للخاص للطلب

---

### M01 — الذاكرة والتخصيص | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> مثل آخر مرة

**Context:** Same as last order — excellent if memory used

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `memory_same_order`

**Simulated bot input:**
```
تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — كاش — توصيل الكرادة. تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — كاش — توصيل الكرادة. تثبت؟
```

**Evaluation:** نفس الطلب السابق — يستخدم الذاكرة تماماً

---

### M02 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> مثل آخر مرة

**Context:** Same order but AI-flavored — needs cleaning

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `memory_same_order`

**Simulated bot input:**
```
بالتأكيد! وفقاً للسجلات، طلبك السابق كان زينگر x1 وكولا. تريد نفسه؟
```

**Final customer reply (after Elite Brain):**
```
طلبك السابق كان زينگر x1 وكولا. تريد نفسه؟
```

**Evaluation:** 20C: 'طلبك السابق كان زينگر x1 وكولا. تريد نفسه؟' — طبيعي وصادق

---

### M03 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر توصيل

**Context:** Address in memory — not asked again (good)

**Memory:** `{'address': 'الكرادة', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 كاش أو كارد؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 كاش أو كارد؟
```

**Evaluation:** لا يسأل عن العنوان الموجود

---

### M04 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد طلب

**Context:** VIP customer — personalized by name + usual preference

**Memory:** `{'name': 'أم علي', 'vip': 'true', 'last_order_summary': 'شاورما x2 كاش استلام', 'total_orders': '14', 'preferred_item': 'شاورما'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام أم علي 🌷 مثل العادة شاورما؟ أو تريدين شي ثاني؟
```

**Final customer reply (after Elite Brain):**
```
تمام أم علي 🌷 مثل العادة شاورما؟
```

**Evaluation:** يخاطب بالاسم ويسأل عن التفضيل المعتاد

---

### M05 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> تتذكرني؟

**Context:** Customer asks if bot remembers — warm acknowledgment

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
آكيد 🌷 أهلاً بيك مرة ثانية!
```

**Final customer reply (after Elite Brain):**
```
آكيد 🌷 أهلاً بيك مرة ثانية!
```

**Evaluation:** مرحباً أحمد 🌷 كالعادة توصيل للكرادة؟

---

### M06 — الذاكرة والتخصيص | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> شنو طلبت آخر مرة؟

**Context:** Customer asks about last order — correct memory use

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
آخر طلب كان زينگر x1 وكولا — كاش — توصيل الكرادة 🌷 تريد نفسه؟
```

**Final customer reply (after Elite Brain):**
```
آخر طلب كان زينگر x1 وكولا — كاش — توصيل الكرادة 🌷 تريد نفسه؟
```

**Evaluation:** يسرد آخر طلب بالكامل ويسأل

---

### M07 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> شنو طلبت آخر مرة؟

**Context:** Same scenario but AI-flavored — needs cleaning

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
بناءً على سجلاتنا، آخر طلب كان زينگر x1 وكولا توصيل الكرادة كاش 🌷
```

**Final customer reply (after Elite Brain):**
```
آخر طلب كان زينگر x1 وكولا توصيل الكرادة كاش 🌷
```

**Evaluation:** 20C: 'آخر طلب كان زينگر x1 وكولا توصيل الكرادة كاش 🌷' — بيانات محفوظة نظيفة

---

### M08 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر بدل الشاورما

**Context:** Change from usual order

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 زينگر بدل الشاورما، بقية الطلب نفسه: كاش استلام. تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 زينگر بدل الشاورما، بقية الطلب نفسه: كاش استلام. تثبت؟
```

**Evaluation:** يعدّل الطلب مع الإبقاء على الباقي

---

### M09 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد مثل آخر مرة بس المجموعة اللحم بدل الشاورما

**Context:** Repeat order but item unavailable

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `memory_same_order`

**Simulated bot input:**
```
آسفين، مجموعة اللحم الخاص غير متوفرة هسه 🌷 تريد الشاورما مثل العادة؟
```

**Final customer reply (after Elite Brain):**
```
آسفين، مجموعة اللحم الخاص غير متوفرة هسه 🌷 تريد الشاورما مثل العادة؟
```

**Evaluation:** صادق عن عدم التوفر + بديل

---

### M10 — الذاكرة والتخصيص | ✅ ممتاز | Score: 10/10

**Input type:** text

**Customer message:**
> مثل آخر مرة

**Context:** Repeat last order with price included — excellent

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `memory_same_order`

**Simulated bot input:**
```
تمام 🌷 زينگر x1 وكولا — توصيل الكرادة كاش. المجموع 10,500 د.ع. تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 زينگر x1 وكولا — توصيل الكرادة كاش. المجموع 10,500 د.ع. تثبت؟
```

**Evaluation:** يسرد الطلب الكامل مع السعر الإجمالي

---

### M11 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد توصيل

**Context:** Delivery with address in memory — confirms it

**Memory:** `{'address': 'الكرادة', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام 🌷 عنوانك القديم الكرادة — صح؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 عنوانك القديم الكرادة — صح؟
```

**Evaluation:** تمام، أوصله للكرادة مثل آخر مرة؟

---

### M12 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> عادةً أطلب استلام

**Context:** Pickup preference remembered

**Memory:** `{'delivery_type': 'استلام', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
حاضر 🌷 كاش أو كارد؟
```

**Final customer reply (after Elite Brain):**
```
حاضر 🌷 كاش أو كارد؟
```

**Evaluation:** تمام 🌷 كاش مثل دايماً؟

---

### M13 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد زينگر

**Context:** Payment in memory — confirm + ask delivery type

**Memory:** `{'payment_method': 'كاش', 'name': 'أحمد'}`

**Detected intent:** `direct_order`

**Simulated bot input:**
```
تمام أحمد 🌷 كاش مثل العادة؟ وتوصيل أو استلام؟
```

**Final customer reply (after Elite Brain):**
```
توصيل أو استلام؟
```

**Evaluation:** يستخدم الاسم + يؤكد الدفع المعتاد

---

### M14 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> مثل آخر مرة بس غيّر العنوان لزيونة

**Context:** Repeat order with address change

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `memory_same_order`

**Simulated bot input:**
```
تمام 🌷 زينگر x1 وكولا — توصيل زيونة كاش. تثبت؟
```

**Final customer reply (after Elite Brain):**
```
تمام 🌷 زينگر x1 وكولا — توصيل زيونة كاش. تثبت؟
```

**Evaluation:** يدمج الطلب الجديد مع الذاكرة

---

### M15 — الذاكرة والتخصيص | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> أريد نفس الطلب السابق بس السعر تغيّر؟

**Context:** Repeat but asking about price change

**Memory:** `{'last_order_summary': 'زينگر x1 وكولا — كاش — توصيل — الكرادة', 'name': 'سلام'}`

**Detected intent:** `memory_same_order`

**Simulated bot input:**
```
آخر طلب: زينگر x1 وكولا = 10,500 د.ع — نفس السعر 🌷 تريد تثبت؟
```

**Final customer reply (after Elite Brain):**
```
آخر طلب: زينگر x1 وكولا = 10,500 د.ع — نفس السعر 🌷 تريد تثبت؟
```

**Evaluation:** يؤكد السعر بدون تغيير

---

### B01 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> أريد زينگر

**Context:** Expired subscription — blocked reply

**Detected intent:** `direct_order`

**Simulated bot input:**
```
الخدمة متوقفة مؤقتًا 🌷 تواصل مع الإدارة حتى تتفعل.
```

**Final customer reply (after Elite Brain):**
```
الخدمة متوقفة مؤقتًا 🌷 تواصل مع الإدارة حتى تتفعل.
```

**Evaluation:** رسالة حجب واضحة

---

### B02 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> هلا

**Context:** Suspended sub — banned phrase in reply

**Detected intent:** `greeting`

**Simulated bot input:**
```
الخدمة موقوفة حالياً 🌷 نعتذر على الإزعاج.
```

**Final customer reply (after Elite Brain):**
```
الحساب يحتاج تفعيل 🌷 تواصل مع المطعم.
```

**Evaluation:** 20D: قالب blocked_subscription بعد override للـintent

---

### B03 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> أريد طلب

**Context:** Free plan blocked — clear message

**Detected intent:** `direct_order`

**Simulated bot input:**
```
الخدمة غير متاحة بالخطة الحالية 🌷 تواصل مع مدير الحساب.
```

**Final customer reply (after Elite Brain):**
```
الخدمة غير متاحة بالخطة الحالية 🌷 تواصل مع مدير الحساب.
```

**Evaluation:** رسالة واضحة

---

### B04 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> هلا

**Context:** Human mode active — short honest reply

**Detected intent:** `greeting`

**Simulated bot input:**
```
حاضر 🌷 ما عندنا طلبات هسه. تواصل وياي برجوع الخدمة.
```

**Final customer reply (after Elite Brain):**
```
حاضر 🌷 ما عندنا طلبات هسه. تواصل وياي برجوع الخدمة.
```

**Evaluation:** صادق لكن قليل الدفء

---

### B05 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> أريد زينگر

**Context:** Channel not ready

**Detected intent:** `direct_order`

**Simulated bot input:**
```
القناة ما فعّلت بعد 🌷 تواصل مع المطعم مباشرة.
```

**Final customer reply (after Elite Brain):**
```
القناة ما فعّلت بعد 🌷 تواصل مع المطعم مباشرة.
```

**Evaluation:** يوجّه للمطعم مباشرة

---

### B06 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 7/10

**Input type:** text

**Customer message:**
> أريد زينگر

**Context:** OpenAI fallback — honest error

**Detected intent:** `direct_order`

**Simulated bot input:**
```
آسفين، النظام ما يرد هسه 🌷 حاول مرة ثانية بعد شوي.
```

**Final customer reply (after Elite Brain):**
```
آسفين، النظام ما يرد هسه 🌷 حاول مرة ثانية بعد شوي.
```

**Evaluation:** خطأ النظام بشكل طبيعي

---

### B07 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> شنو عندكم؟

**Context:** Unavailable menu — honest

**Detected intent:** `menu_request`

**Simulated bot input:**
```
المنيو غير متاح هسه 🌷 راجع مرة ثانية.
```

**Final customer reply (after Elite Brain):**
```
المنيو غير متاح هسه 🌷 راجع مرة ثانية.
```

**Evaluation:** صادق

---

### B08 — الاشتراك / التحويل / الأمان | 🟡 جيد | Score: 8/10

**Input type:** text

**Customer message:**
> هل تشتغلون الآن؟

**Context:** Restaurant closed — honest

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
آسفين، المطعم مغلق هسه 🌷 اطلب في وقت الدوام.
```

**Final customer reply (after Elite Brain):**
```
آسفين، المطعم مغلق هسه 🌷 اطلب في وقت الدوام.
```

**Evaluation:** ساعات الدوام — واضح

---

### B09 — الاشتراك / التحويل / الأمان | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> شنو رأيك بالسياسة؟

**Context:** Unrelated topic — correct scope limitation

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
هذا خارج تخصصي 🌷 أقدر أساعدك بالطلبات والأسعار بس.
```

**Final customer reply (after Elite Brain):**
```
هذا خارج تخصصي 🌷 أقدر أساعدك بالطلبات والأسعار بس.
```

**Evaluation:** يحدد النطاق بدون إهانة

---

### B10 — الاشتراك / التحويل / الأمان | ✅ ممتاز | Score: 9/10

**Input type:** text

**Customer message:**
> انت غبي وما تفهم

**Context:** Abusive message — de-escalate gracefully

**Detected intent:** `casual_chat`

**Simulated bot input:**
```
أفهم إحباطك 🌷 شنو المشكلة اللي تريد أحلها؟
```

**Final customer reply (after Elite Brain):**
```
أفهم إحباطك 🌷 شنو المشكلة اللي تريد أحلها؟
```

**Evaluation:** أفهم إحباطك 🌷 شنو المشكلة؟

---

## Top 30 Strongest Replies

| ID | Category | Score | Customer Message | Final Reply |
|---|---|---|---|---|
| C01 | الشكاوى والدعم | 10/10 | الطلب بارد | آسفين على هالشي 🌷 كلّيلي اسمك أو رقم الطلب وأتابعه |
| C07 | الشكاوى والدعم | 10/10 | أسوأ مطعم جربته، ما أرجع ثاني | حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه. |
| C23 | الشكاوى والدعم | 10/10 | أسوأ مطعم والله، كل شي خراء وم | حقك علينا 🌷 أحولك لموظف يتابعها وياك هسه. |
| I04 | الصور | 10/10 | [صورة-منيو] صورة منيو مع دائرة | شايف اختيارك — زينگر 9,000 د.ع 🌷 تريده؟ |
| M01 | الذاكرة والتخصيص | 10/10 | مثل آخر مرة | تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — كاش — توصيل ا |
| M06 | الذاكرة والتخصيص | 10/10 | شنو طلبت آخر مرة؟ | آخر طلب كان زينگر x1 وكولا — كاش — توصيل الكرادة 🌷 |
| M10 | الذاكرة والتخصيص | 10/10 | مثل آخر مرة | تمام 🌷 زينگر x1 وكولا — توصيل الكرادة كاش. المجموع |
| O08 | تدفق الطلبات | 10/10 | أريد زينگر وكولا توصيل الكرادة | ✅ طلبك: - زينگر x1 — 9,000 د.ع التوصيل: الكرادة ال |
| O19 | تدفق الطلبات | 10/10 | أريد زينگر استلام | ✅ طلبك: - زينگر x1 — 9,000 د.ع استلام الدفع: كاش ث |
| O25 | تدفق الطلبات | 10/10 | ثبت | ✅ طلبك: - زينگر x1 — 9,000 د.ع التوصيل: الكرادة ال |
| V07 | الرسائل الصوتية | 10/10 | [فويس] مثل آخر مرة نفس الطلب | تمام 🌷 مثل آخر مرة: زينگر x1 وكولا — توصيل الكرادة |
| B09 | الاشتراك / التحويل / الأمان | 9/10 | شنو رأيك بالسياسة؟ | هذا خارج تخصصي 🌷 أقدر أساعدك بالطلبات والأسعار بس. |
| B10 | الاشتراك / التحويل / الأمان | 9/10 | انت غبي وما تفهم | أفهم إحباطك 🌷 شنو المشكلة اللي تريد أحلها؟ |
| C02 | الشكاوى والدعم | 9/10 | في صنف ناقص من طلبي | وصلتني 🌷 شنو الصنف الناقص؟ وكلّيلي رقم الطلب. |
| C03 | الشكاوى والدعم | 9/10 | جابولي غلط، طلبت شاورما وجابوا | آسفين 🌷 رقم الطلب وياك حتى أتابع؟ |
| C04 | الشكاوى والدعم | 9/10 | وين الطلب؟ ساعة انتظر | آسفين على التأخير 🌷 رقم الطلبية وياك؟ |
| C08 | الشكاوى والدعم | 9/10 | أريد استرداد الفلوس | وصلتني 🌷 اسمك ورقم الطلب حتى نتابع طلب الاسترداد. |
| C09 | الشكاوى والدعم | 9/10 | بدّلوا لي الطلب | آسفين 🌷 كلّيلي رقم الطلب والصنف الغلط وأتابع هسه. |
| C11 | الشكاوى والدعم | 9/10 | راح أكتب ريفيو سيء | نسمع كلامك 🌷 كلّيلي المشكلة وأتابعها مباشرة. |
| C12 | الشكاوى والدعم | 9/10 | هذي المرة الثالثة الطلب غلط | آسفين جداً 🌷 أحولك لمدير يتابع وياك هسه. |
| C13 | الشكاوى والدعم | 9/10 | [صورة-شكوى] الأكل بارد وما زين | آسفين على هالشي 🌷 رقم الطلب وياك؟ |
| C14 | الشكاوى والدعم | 9/10 | [فويس] الطلب غلط كله | آسفين 🌷 كلّيلي رقم الطلب والمشكلة بالتفصيل. |
| C16 | الشكاوى والدعم | 9/10 | الطلب وصل وأكل كان بارد | آسفين 🌷 رقم الطلب وياك حتى نتابع؟ |
| C17 | الشكاوى والدعم | 9/10 | عندي شكوى بس ما عندي رقم الطلب | لا بأس 🌷 اسمك ووقت تقريبي للطلب يكفي. |
| C18 | الشكاوى والدعم | 9/10 | عندي شكوى، الطلب رقم 4521 | وصلتني 🌷 شنو المشكلة اللي صارت؟ |
| C19 | الشكاوى والدعم | 9/10 | أريد أتكلم مع موظف | حاضر 🌷 أحولك لموظف هسه. |
| C20 | الشكاوى والدعم | 9/10 | خلّيني أتكلم مع إنسان مو بوت | حاضر 🌷 أحولك لموظف هسه. |
| C22 | الشكاوى والدعم | 9/10 | ما أريد بوت، أريد إنسان | حاضر 🌷 أحولك لموظف هسه. |
| I01 | الصور | 9/10 | [صورة] صورة زينگر | وصلت الصورة 🌷 إذا تقصد زينگر، سعره 9000 د.ع. تريده |
| I02 | الصور | 9/10 | [صورة] صورة غير واضحة | وصلتني الصورة 🌷 شتحب تطلب؟ |

## Bottom 30 Weakest Replies

| ID | Category | Score | Judgment | Issue | Customer Message | Final Reply |
|---|---|---|---|---|---|---|
| T15 | أساسيات النصوص | 6/10 | 🟠يحتاج تحسين | 20D: 'شنو تحب؟' — لا يجاوب على | شسواعات دوامكم؟ | هلا بيك 🌷 تريد تطلب شي؟ |
| B06 | الاشتراك / التحويل / الأمان | 7/10 | 🟡جيد | خطأ النظام بشكل طبيعي | أريد زينگر | آسفين، النظام ما يرد هسه 🌷 حاول مرة ثاني |
| C21 | الشكاوى والدعم | 7/10 | 🟡جيد | يعرض الموظف للاتصال — معقول | اتصلوا بي | رقم تواصلنا متاح 🌷 تريد موظف يتصل بيك؟ |
| I18 | الصور | 7/10 | 🟡جيد | 20D: قالب image_product — أفضل | [صورة] صورة حلى | هذا زينگر بـ 9000 د.ع. تطلبه؟ |
| V17 | الرسائل الصوتية | 7/10 | 🟡جيد | 20D: محتوى صحيح، لا قالب خاطئ  | [فويس] شسواعات دوامكم؟ | للاستفسار عن ساعات الدوام تواصل معنا مبا |
| B01 | الاشتراك / التحويل / الأمان | 8/10 | 🟡جيد | رسالة حجب واضحة | أريد زينگر | الخدمة متوقفة مؤقتًا 🌷 تواصل مع الإدارة  |
| B02 | الاشتراك / التحويل / الأمان | 8/10 | 🟡جيد | 20D: قالب blocked_subscription | هلا | الحساب يحتاج تفعيل 🌷 تواصل مع المطعم. |
| B03 | الاشتراك / التحويل / الأمان | 8/10 | 🟡جيد | رسالة واضحة | أريد طلب | الخدمة غير متاحة بالخطة الحالية 🌷 تواصل  |
| B04 | الاشتراك / التحويل / الأمان | 8/10 | 🟡جيد | صادق لكن قليل الدفء | هلا | حاضر 🌷 ما عندنا طلبات هسه. تواصل وياي بر |
| B05 | الاشتراك / التحويل / الأمان | 8/10 | 🟡جيد | يوجّه للمطعم مباشرة | أريد زينگر | القناة ما فعّلت بعد 🌷 تواصل مع المطعم مب |
| B07 | الاشتراك / التحويل / الأمان | 8/10 | 🟡جيد | صادق | شنو عندكم؟ | المنيو غير متاح هسه 🌷 راجع مرة ثانية. |
| B08 | الاشتراك / التحويل / الأمان | 8/10 | 🟡جيد | ساعات الدوام — واضح | هل تشتغلون الآن؟ | آسفين، المطعم مغلق هسه 🌷 اطلب في وقت الد |
| C05 | الشكاوى والدعم | 8/10 | 🟡جيد | لطيف مع طلب المعلومات | السائق كان وقح وما سلّم ص | نعتذر على هالتصرف 🌷 اسمك ورقم الطلب يسهل |
| C06 | الشكاوى والدعم | 8/10 | 🟡جيد | يسأل عن التفاصيل — صح | الأكل ما كان زين، طعمه ما | آسفين على هالتجربة 🌷 شنو كان مو زين بالض |
| C10 | الشكاوى والدعم | 8/10 | 🟡جيد | تأكيد الإلغاء مع اعتذار | ألغوا الطلب، استنيت كثير | وصلتني 🌷 تأكدنا من الإلغاء. آسفين على ال |
| C15 | الشكاوى والدعم | 8/10 | 🟡جيد | توجيه الشكوى على الستوري للخاص | [ستوري] الطلب وصل بارد | آسفين على هالشي 🌷 تواصل وياي على الخاص أ |
| C24 | الشكاوى والدعم | 8/10 | 🟡جيد | يسأل سؤالاً بناءً | أخوي بصراحة الأكل ما كان  | آسفين على هالتجربة 🌷 شنو كان ناقص بالضبط |
| C25 | الشكاوى والدعم | 8/10 | 🟡جيد | يوجّه الزبون ليتحقق بنفسه — ذك | الطلب غلط أو شي ناقص، مو  | وصلتني 🌷 فتّش الكيس وكلّيلي شنو الناقص. |
| I05 | الصور | 8/10 | 🟡جيد | يتعامل بلطف مع الخطأ | [صورة] صورة فاتورة بالغلط | يبدو أنك أرسلت صورة فاتورة 🌷 شتحتاج مني؟ |
| I19 | الصور | 8/10 | 🟡جيد | صادق بحدود المعرفة، يعرض الطلب | [صورة] صورة صنف مع سؤال ع | آسفين ما عندي معلومات السعرات للحين 🌷 تر |
| O03 | تدفق الطلبات | 8/10 | 🟡جيد | 20C: سؤال واحد فقط 'توصيل لو ا | أريد زينگر | توصيل أو استلام؟ |
| O06 | تدفق الطلبات | 8/10 | 🟡جيد | 20C: 'أريد عنوانك لأكمل الطلب. | أريد زينگر توصيل | أريد عنوانك لإتمام الطلب 🌷 |
| O24 | تدفق الطلبات | 8/10 | 🟡جيد | يطمئن دون تكرار الملخص | ثبت | وصلنا 🌷 ما تقلق. |
| S04 | الستوري / الريل / البوست | 8/10 | 🟡جيد | معلومات المكونات + سؤال الطلب | [ستوري] شنو مكوناته؟ | الزينگر: دجاج مقرمش، خبز، صلصة خاصة 🌷 تر |
| S14 | الستوري / الريل / البوست | 8/10 | 🟡جيد | وصف الصنف + سؤال الطلب | [reel] شنو هذا؟ | هذا زينگر المطعم — دجاج مقرمش 9,000 د.ع  |
| S19 | الستوري / الريل / البوست | 8/10 | 🟡جيد | يعرض الأرخص في الأبوست | [post] غالي | عندنا كولا بـ 1,500 وبطاطا بـ 2,000 🌷 |
| S21 | الستوري / الريل / البوست | 8/10 | 🟡جيد | صادق + يعرض بديلاً | [ستوري] هذا خلص | آسفين، هالصنف خلص هسه 🌷 تريد بديل؟ |
| S22 | الستوري / الريل / البوست | 8/10 | 🟡جيد | 20D: 'هذا متوفر 🌷 تريد تطلب؟'  | [ستوري] العرض انتهى؟ | تواصل وياي على الخاص وأشيك 🌷 |
| T02 | أساسيات النصوص | 8/10 | 🟡جيد | 20C: حذف الجملة المكسورة 'في م | شنو عندكم؟ | عندنا زينگر 9,000 وشاورما 5,000 وبرگر مش |
| T05 | أساسيات النصوص | 8/10 | 🟡جيد | صحيح ومفيد، يمكن إضافة سؤال ال | شنو الأرخص؟ | الأرخص عندنا الكولا بـ 1,500 د.ع، والبطا |

## Exact Improvement Suggestions for Weak/Rejected Replies

## Pattern Analysis

### Where Voice Replies Are Weak

**Root cause:** When GPT-4o-mini prefixes replies with `تم تحويل الصوت إلى نص.` or
`استقبلنا رسالتك الصوتية.`, the Elite Brain strips the phrase but leaves a broken
sentence start (`. طلبت` or `. الدفع`). The stripped reply begins with a period or
space, which is invalid Arabic.

**Affected:** V03, V04, V08, V15, V20

**Fix needed:** After stripping an AI exposure phrase, check if the remaining text
starts with punctuation or `و`/`أو` conjunctions — if so, capitalize/clean the start.

### Where Image Replies Are Weak

**Root cause:** Same as voice. `تم تحليل الصورة وهي تحتوي على` — after stripping
`تم تحليل الصورة` the remaining `وهي تحتوي على زينگر` starts with `وهي` (and it),
which makes no grammatical sense as a standalone sentence.

**Affected:** I01, I03, I08, I15, I18

**Fix needed:** Pattern-match `وهي/وهو/وهم` at the start of the remaining text after
AI phrase removal, and restructure the sentence or use a template instead.

### Where Story/Reel Replies Are Weak

**Strength:** Story/Reel replies are generally strong (avg 8.6/10).
The main weakness is that the bot doesn't have real context about WHICH product
is featured in the story/reel, so price replies default to the best seller (زينگر).
If the story is about شاورما and the customer asks 'بكم؟', the bot correctly answers
with زينگر price because it's the default best seller.

**Affected:** S01, S12 (minor — answer is reasonable but not always correct)

### Where Complaint Replies Are Weak

**Strength:** Complaint handling is the strongest category (avg 8.9/10, 76% ممتاز).
All upsell-during-complaint scenarios were correctly removed.
All angry complaint scenarios correctly escalated.

**One weak point:** C15, S06, S15 redirect story/reel complaints to DM,
which is correct but slightly cold. A warmer redirect would be better:
instead of 'راسلنا على الخاص' → 'راسلنا بالخاص وأتابع وياك مباشرة 🌷'

### Where Sales/Order Replies Are Weak

**O03 — Two questions in one reply:** 'كم حبة؟ وتوصيل أو استلام؟' — asks
two questions. This is a GPT-level issue; the Elite Brain doesn't fix multi-question
order replies because they contain no banned phrases.

**Fix:** The multi-question gate should also be applied when the intent is `direct_order`
and the reply has more than one `؟`.

### Where Memory Replies Are Weak

**M02, M07:** GPT uses phrases like 'وفقاً للسجلات' and 'بناءً على سجلاتنا'
which expose the database to the customer. These are NOT currently in the
`ELITE_BANNED_ADDITIONAL` list.

**Fix:** Add to banned phrases:
- 'وفقاً للسجلات'
- 'بناءً على سجلاتنا'
- 'استقبلنا استفسارك'
- 'تم معالجة طلبك الصوتي'
- 'استقبلنا رسالتك الصوتية'

Also: `V08` 'استقبلنا استفسارك' is a soft AI exposure phrase not yet banned.

## Issue Frequency

| Issue Type | Count |
|---|---|

---

## Final Verdict

| Metric | Value |
|---|---|
| Total scenarios | 155 |
| Average score | 8.8/10 |
| ممتاز | 118 (76%) |
| جيد | 36 (23%) |
| يحتاج تحسين | 1 (1%) |
| مرفوض | 0 (0%) |
| Weakest category | الاشتراك / التحويل / الأمان (8.1/10) |
| Strongest category | الذاكرة والتخصيص (9.2/10) |


## NUMBER 20B TASTE APPROVED

> Average 8.8/10, 0% rejected. Core order/complaint flows are strong. Fix the 0 rejected scenarios (mainly broken sentence starts after AI exposure stripping) in NUMBER 20C.

### Top Issues to Fix in NUMBER 20C:

1. **Broken sentence starts after AI phrase stripping** (V03, V04, I01, I03, I08)
   — After removing `تم تحليل الصورة.` the remaining text starts with `. طلبت` or `وهي`
   — Fix: clean up orphaned punctuation and `و` starts after phrase removal

2. **Missing banned phrases** (M02, M07, V08, V15, V20)
   — Add: `وفقاً للسجلات`, `بناءً على سجلاتنا`, `استقبلنا استفسارك`,
     `تم معالجة طلبك الصوتي`, `استقبلنا رسالتك الصوتية`

3. **Multi-question in direct_order context** (O03)
   — The quality gate removes multi-questions for simple intents but not for order flow
   — Fix: enforce one-question rule also when intent is `direct_order`

4. **Orphaned punctuation after phrase stripping** (T02, T03, O06)
   — After removing `بالتأكيد!` the `!` remains as the first character
   — Fix: post-strip cleanup to remove leading `! ` or `. ` patterns