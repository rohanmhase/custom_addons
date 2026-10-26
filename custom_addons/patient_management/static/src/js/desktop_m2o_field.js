/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Many2OneField, many2OneField } from "@web/views/fields/many2one/many2one_field";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";
import { useSubEnv } from "@odoo/owl";

export class ReferralMany2OneField extends Many2OneField {
    setup() {
        // Force the child Many2XAutocomplete component to treat this field as Desktop
        useSubEnv({ isSmall: false });
        super.setup();
    }

    get Many2XAutocompleteProps() {
        return {
            ...super.Many2XAutocompleteProps,
            noSearchMore: true,
        };
    }
}

export const referralMany2OneField = {
    ...many2OneField,
    component: ReferralMany2OneField,
};

registry.category("fields").add("referral_desktop_m2o", referralMany2OneField);