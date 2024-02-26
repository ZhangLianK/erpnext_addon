import copy
from collections import OrderedDict
import frappe
from frappe import _, qb
from frappe.query_builder import CustomFunction
from frappe.query_builder.functions import Max
from frappe.utils import date_diff, flt, getdate

def get_conditions(sales_order_name):
	conditions = "and so.name = '{0}'".format(sales_order_name)
	return conditions


def get_data(conditions):
	data = frappe.db.sql(
		"""
		SELECT
			so.transaction_date as date,
            so.order_note as order_note,
			soi.delivery_date as delivery_date,
			so.name as sales_order,
			so.status, so.customer, soi.item_code,
            so.advance_paid as advance_paid,
			DATEDIFF(CURRENT_DATE, soi.delivery_date) as delay_days,
			IF(so.status in ('Completed','To Bill'), 0, (SELECT delay_days)) as delay,
			soi.qty, soi.delivered_qty,
			(soi.qty - soi.delivered_qty) AS pending_qty,
			IFNULL(SUM(sii.qty), 0) as billed_qty,
			soi.base_amount as amount,
			(soi.delivered_qty * soi.base_rate) as delivered_qty_amount,
			(soi.billed_amt * IFNULL(so.conversion_rate, 1)) as billed_amount,
			(soi.base_amount - (soi.billed_amt * IFNULL(so.conversion_rate, 1))) as pending_amount,
			soi.warehouse as warehouse,
			so.company, soi.name,
			soi.description as description
		FROM
			`tabSales Order` so,
			`tabSales Order Item` soi
		LEFT JOIN `tabSales Invoice Item` sii
			ON sii.so_detail = soi.name and sii.docstatus = 1
		WHERE
			soi.parent = so.name
			and so.status not in ('Stopped', 'Closed', 'On Hold')
			and so.docstatus = 1
			{conditions}
		GROUP BY soi.name
		ORDER BY so.transaction_date ASC, soi.item_code ASC
	""".format(
			conditions=conditions
		),
		as_dict=1,
	)

	return data


def get_so_elapsed_time(data):
	"""
	query SO's elapsed time till latest delivery note
	"""
	so_elapsed_time = OrderedDict()
	if data:
		sales_orders = [x.sales_order for x in data]

		so = qb.DocType("Sales Order")
		soi = qb.DocType("Sales Order Item")
		dn = qb.DocType("Delivery Note")
		dni = qb.DocType("Delivery Note Item")

		to_seconds = CustomFunction("TO_SECONDS", ["date"])

		query = (
			qb.from_(so)
			.inner_join(soi)
			.on(soi.parent == so.name)
			.left_join(dni)
			.on(dni.so_detail == soi.name)
			.left_join(dn)
			.on(dni.parent == dn.name)
			.select(
				so.name.as_("sales_order"),
				soi.item_code.as_("so_item_code"),
				(to_seconds(Max(dn.posting_date)) - to_seconds(so.transaction_date)).as_("elapsed_seconds"),
			)
			.where((so.name.isin(sales_orders)) & (dn.docstatus == 1))
			.orderby(so.name, soi.name)
			.groupby(soi.name)
		)
		dn_elapsed_time = query.run(as_dict=True)

		for e in dn_elapsed_time:
			key = (e.sales_order, e.so_item_code)
			so_elapsed_time[key] = e.elapsed_seconds

	return so_elapsed_time


def prepare_data(data, so_elapsed_time):
	completed, pending = 0, 0

	if True:
		sales_order_map = {}

	for row in data:
		# sum data for chart
		completed += row["billed_amount"]
		pending += row["pending_amount"]

		# prepare data for report view
		row["qty_to_bill"] = flt(row["qty"]) - flt(row["billed_qty"])

		row["delay"] = 0 if row["delay"] and row["delay"] < 0 else row["delay"]

		row["time_taken_to_deliver"] = (
			so_elapsed_time.get((row.sales_order, row.item_code))
			if row["status"] in ("To Bill", "Completed")
			else 0
		)

		if True:
			so_name = row["sales_order"]

			if not so_name in sales_order_map:
				# create an entry
				row_copy = copy.deepcopy(row)
				sales_order_map[so_name] = row_copy
			else:
				# update existing entry
				so_row = sales_order_map[so_name]
				so_row["required_date"] = max(getdate(so_row["delivery_date"]), getdate(row["delivery_date"]))
				so_row["delay"] = min(so_row["delay"], row["delay"])

				# sum numeric columns
				fields = [
					"qty",
					"delivered_qty",
					"pending_qty",
					"billed_qty",
					"qty_to_bill",
					"amount",
					"delivered_qty_amount",
					"billed_amount",
					"pending_amount",
				]
				for field in fields:
					so_row[field] = flt(row[field]) + flt(so_row[field])

	if True:
		data = []
		for so in sales_order_map:
			data.append(sales_order_map[so])
		return data

	return data


def get_sales_order_data(sales_order_name):
    conditions = get_conditions(sales_order_name)
    data = get_data(conditions)
    so_elapsed_time = get_so_elapsed_time(data)

    if not data:
        return [], [], None, []

    sales_order_data = prepare_data(data, so_elapsed_time)[0]
    sales_order_data['rate'] = sales_order_data['amount'] / sales_order_data['qty']

    
    # Fetch sales invoice which is refer to this sales order
    sales_invoices = frappe.get_all('Sales Invoice',
                                    fields=['name'],
                                    filters=[['docstatus', '=', 1], ['Sales Invoice Item','sales_order', '=', sales_order_name]])
    #get all payments which is reference to this sales order or above sales invoices

    scale_items = frappe.get_all('Scale Item', 
                              fields=['stock_date', 'vehicle', 'load_net_weight', 'offload_net_weight', 'delivery_note','item','bill_type'],
                              filters={'sales_order': sales_order_name, 'docstatus': 1},)
    payments = []
    references = []
    #add all sales invoices and the sales order itself to the references
    references.append(sales_order_name)
    references += [sales_invoice.name for sales_invoice in sales_invoices]
    
    

    #create a conditions using references
    conditions = " and per.reference_name in ('{0}')".format("','".join(references)) 
    #get all payments entry references' allocations ,reference doctype, reference name, and the parent payment entry's mode_of_payment, posting date
    #using db sql
    payments = frappe.db.sql("""
        SELECT
            per.reference_doctype,
            per.reference_name,
            pe.mode_of_payment,
            pe.posting_date,
            per.allocated_amount as paid_amount
        FROM
            `tabPayment Entry Reference` per
        INNER JOIN `tabPayment Entry` pe
            ON per.parent = pe.name
        WHERE
            pe.docstatus = 1
            and per.reference_doctype in ('Sales Invoice', 'Sales Order')
            and per.parenttype = 'Payment Entry'
            {conditions}
    """.format(conditions=conditions), as_dict=1)
    
    #loop the payments if the reference doctype is a sales invoice, set the clear status field to 已核销
    for payment in payments:
        if payment['reference_doctype'] == 'Sales Invoice':
            payment['clear_status'] = '已核销'
        else:
            payment['clear_status'] = '未核销'
    
    #sum the payments as sales order data total payments
    sales_order_data['total_payments'] = sum([payment['paid_amount'] for payment in payments])
    

    html = frappe.render_template('erpnext_addon/templates/sales_order_overview.html', {
        'sales_order': sales_order_data,
        'payments': payments,
        'items': scale_items,
    })
    
    return html

@frappe.whitelist()
def generate_sales_order_report(sales_order_name):
    # Check if the user has permissions to view the report
    if not frappe.has_permission('Sales Order', 'read', sales_order_name):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    
    # Generate the report
    return get_sales_order_data(sales_order_name)
