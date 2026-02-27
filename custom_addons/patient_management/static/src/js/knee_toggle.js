/** @odoo-module **/

import { registry } from "@web/core/registry";
import { BooleanToggle } from "@web/views/fields/boolean_toggle/boolean_toggle";
import { useService } from "@web/core/utils/hooks";

export class LeftKneeToggle extends BooleanToggle {
    async onChange(value) {
        await super.onChange(value);
        if (value) {
            await this.props.record.update({ is_right_knee: false });
        }
    }
}

LeftKneeToggle.template = BooleanToggle.template;
registry.category("fields").add("left_knee_toggle", LeftKneeToggle);

export class RightKneeToggle extends BooleanToggle {
    async onChange(value) {
        await super.onChange(value);
        if (value) {
            await this.props.record.update({ is_left_knee: false });
        }
    }
}

RightKneeToggle.template = BooleanToggle.template;
registry.category("fields").add("right_knee_toggle", RightKneeToggle);