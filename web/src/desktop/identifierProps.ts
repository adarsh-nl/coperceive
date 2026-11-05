// SPDX-License-Identifier: Apache-2.0
import type { ComponentPropsWithRef } from "react";

/**
 * Standard attributes to disable platform text transformations (macOS/iOS auto-capitalization,
 * autocorrection, and spellchecking) for case-sensitive inputs like identifiers, hostnames,
 * usernames, Slurm partitions, and filesystem paths.
 */
export const IDENTIFIER_INPUT_PROPS = {
  autoCapitalize: "off",
  autoCorrect: "off",
  spellCheck: false,
  autoComplete: "off",
} as const;

export type IdentifierInputProps = ComponentPropsWithRef<"input">;
