/** @odoo-module **/
import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class RangeSliderField extends Component {
    static template = 'FieldRangeSlider';
    static props = { ...standardFieldProps };

    setup() {
        // Map scores to their respective emojis
        this.emojis = {
            1: "😄", 2: "🙂", 3: "😐", 4: "😕", 5: "☹️",
            6: "😣", 7: "😖", 8: "😫", 9: "😵", 10: "😭"
        };

        // Initialize the local state
        this.state = useState({
            value: this.props.record.data[this.props.name] || 1,
        });
    }

    // Updates the number/emoji locally while dragging (smooth UI)
    onInput(e) {
        this.state.value = parseInt(e.target.value, 10);
    }

    // Saves the data to the database when the doctor releases the mouse/finger
    onChange(e) {
        const val = parseInt(e.target.value, 10);
        this.state.value = val;
        this.props.record.update({ [this.props.name]: val });
    }
}

// Register the widget in Odoo
export const rangeSliderField = {
    component: RangeSliderField,
    supportedTypes: ["integer"],
};
registry.category("fields").add("vas_slider", rangeSliderField);