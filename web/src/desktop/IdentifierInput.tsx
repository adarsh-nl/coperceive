// SPDX-License-Identifier: Apache-2.0
import React from "react";
import { IDENTIFIER_INPUT_PROPS, type IdentifierInputProps } from "./identifierProps";

export { IDENTIFIER_INPUT_PROPS, type IdentifierInputProps };

/**
 * Shared input element with platform autocorrection and autocapitalization disabled.
 * Uses `IDENTIFIER_INPUT_PROPS` by default.
 */
export const IdentifierInput = React.forwardRef<HTMLInputElement, IdentifierInputProps>(
  (props, ref) => {
    return <input ref={ref} {...IDENTIFIER_INPUT_PROPS} {...props} />;
  },
);

IdentifierInput.displayName = "IdentifierInput";
export default IdentifierInput;
