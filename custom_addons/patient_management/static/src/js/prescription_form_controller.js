/** @odoo-module **/

import { registry } from "@web/core/registry";
import { FormController } from "@web/views/form/form_controller";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched } from "@odoo/owl";

// Patch the FormController for prescription tiles functionality
patch(FormController.prototype, {
    setup() {
        super.setup();

        // Only run for prescription model
        if (this.props.resModel === 'patient.prescription') {
            this.orm = useService("orm");
            this.notification = useService("notification");
            this.medicines = [];

            onMounted(() => {
                console.log('onMounted - Form mounted');
                // Add a small delay to ensure the form is fully rendered
                setTimeout(() => {
                    this.loadMedicineTiles();
                }, 100);
            });

            onPatched(() => {
                console.log('onPatched - Form patched');
                // Check if we have the container and data
                const container = document.getElementById('medicine_tiles_area');
                if (container && this.model?.root?.data?.clinic_id) {
                    // Only load if we haven't loaded medicines yet or clinic changed
                    if (!this.medicines.length || this._lastClinicId !== this.getClinicId()) {
                        this.loadMedicineTiles();
                    } else {
                        // Just re-render existing tiles
                        this.renderMedicineTiles();
                    }
                }
            });
        }
    },

    getClinicId() {
        const record = this.model?.root;
        if (!record || !record.data || !record.data.clinic_id) {
            return null;
        }
        return Array.isArray(record.data.clinic_id)
            ? record.data.clinic_id[0]
            : record.data.clinic_id;
    },

    setupSearchListener() {
        const searchInput = document.getElementById('medicine_search');
        if (searchInput && !searchInput.dataset.listenerAttached) {
            searchInput.addEventListener('input', () => {
                console.log('Search input changed');
                this.renderMedicineTiles();
            });
            searchInput.dataset.listenerAttached = 'true';
            console.log('Search listener attached');
        }
    },

    async loadMedicineTiles() {
        // Only load for prescription model
        if (this.props.resModel !== 'patient.prescription') {
            console.log('Not prescription model, skipping');
            return;
        }

        const record = this.model?.root;

        console.log('loadMedicineTiles - model:', this.model);
        console.log('loadMedicineTiles - record:', record);
        console.log('loadMedicineTiles - record.data:', record?.data);

        // Check if record exists and has clinic_id
        if (!record || !record.data) {
            console.log('No record or record.data found, waiting...');
            return;
        }

        if (!record.data.clinic_id) {
            console.log('No clinic_id found in record.data');
            return;
        }

        const clinic_id = this.getClinicId();
        console.log('Clinic ID:', clinic_id);

        if (!clinic_id) {
            console.log('Clinic ID is empty or invalid');
            return;
        }

        // Store the last clinic ID to avoid reloading unnecessarily
        this._lastClinicId = clinic_id;

        try {
            console.log('Calling get_available_medicines for clinic:', clinic_id);
            this.medicines = await this.orm.call(
                'patient.prescription',
                'get_available_medicines',
                [clinic_id]
            );
            console.log('Medicines loaded:', this.medicines?.length, this.medicines);
            this.renderMedicineTiles();
        } catch (error) {
            console.error("Error loading medicines:", error);
            this.notification.add(
                "Error loading medicines. Check console for details.",
                { type: "danger" }
            );
        }
    },

    renderMedicineTiles() {
        const container = document.getElementById('medicine_tiles_area');
        if (!container) {
            console.log('Medicine tiles container not found in DOM');
            return;
        }

        console.log('Rendering tiles, total medicines:', this.medicines?.length);

        const searchInput = document.getElementById('medicine_search');
        const searchTerm = searchInput ? searchInput.value.toLowerCase() : '';

        const filteredMedicines = (this.medicines || []).filter(med =>
            med.name.toLowerCase().includes(searchTerm)
        );

        console.log('Filtered medicines:', filteredMedicines.length);

        if (filteredMedicines.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted p-4">
                    <i class="fa fa-search fa-2x mb-2"></i>
                    <p>${this.medicines?.length ? 'No medicines match your search' : 'No medicines available'}</p>
                </div>
            `;
            return;
        }

        container.innerHTML = filteredMedicines.map(medicine => `
            <div class="o_medicine_tile ${medicine.qty_available <= 0 ? 'out_of_stock' : ''}"
                 data-product-id="${medicine.id}"
                 data-product-name="${this.escapeHtml(medicine.name)}"
                 data-qty-available="${medicine.qty_available}">
                <div class="medicine_content">
                    <div class="medicine_name">${this.escapeHtml(medicine.name)}</div>
                    <div class="medicine_stock">
                        <span class="badge ${medicine.qty_available > 0 ? 'bg-success' : 'bg-danger'}">
                            Stock: ${medicine.qty_available} ${this.escapeHtml(medicine.uom)}
                        </span>
                    </div>
                </div>
                <div class="medicine_add_btn">
                    <i class="fa fa-plus-circle"></i>
                </div>
            </div>
        `).join('');

        console.log('Tiles HTML rendered, attaching click handlers');

        // Attach click handlers
        container.querySelectorAll('.o_medicine_tile').forEach(tile => {
            tile.addEventListener('click', (e) => this.onMedicineTileClick(e));
        });

        // Setup search listener if not already done
        this.setupSearchListener();
    },

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    async onMedicineTileClick(event) {
        const tile = event.currentTarget;
        const productId = parseInt(tile.dataset.productId);
        const qtyAvailable = parseFloat(tile.dataset.qtyAvailable);
        const productName = tile.dataset.productName;

        console.log('Tile clicked:', { productId, productName, qtyAvailable });

        if (qtyAvailable <= 0) {
            this.notification.add(
                "This medicine is out of stock!",
                { type: "warning" }
            );
            return;
        }

        // Get current record
        const record = this.model?.root;

        if (!record) {
            console.error('No record found');
            this.notification.add(
                "Error: Form data not available",
                { type: "danger" }
            );
            return;
        }

        console.log('Current record:', record);
        console.log('Current line_ids:', record.data?.line_ids);

        // Check if medicine already in prescription
        const lineIds = record.data?.line_ids;

        if (lineIds && lineIds.records) {
            const existingLine = lineIds.records.find(lineRecord => {
                const lineProductId = Array.isArray(lineRecord.data.product_id)
                    ? lineRecord.data.product_id[0]
                    : lineRecord.data.product_id;
                return lineProductId === productId;
            });

            if (existingLine) {
                await existingLine.update({
                    qty: existingLine.data.qty + 1
                });
                this.notification.add(`${productName} quantity increased`, { type: "info" });
                return;
            }
        }

        // Add new line using the proper Odoo 17 format
        try {
            console.log('Adding medicine to prescription...');

            // Use the record's update method with command format
            await record.update({
                line_ids: [
                    [0, 0, {
                        product_id: [productId,productName],
                        qty: 1.0,
                    }]
                ]
            });

            console.log('Medicine added successfully');

            this.notification.add(
                `${productName} added to prescription!`,
                { type: "success" }
            );

            // Visual feedback
            tile.classList.add('tile_added');
            setTimeout(() => {
                tile.classList.remove('tile_added');
            }, 500);

        } catch (error) {
            console.error("Error adding medicine:", error);

            this.notification.add(
                "Error adding medicine: " + (error.message || error),
                { type: "danger" }
            );
        }
    }
});