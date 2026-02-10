/**
 * Form validation utilities for real-time validation.
 *
 * This module provides:
 * - Zod schemas for common form fields
 * - Real-time validation hooks
 * - Form state management with validation
 * - Debounced async validation
 */

import { z } from 'zod';
import { useState, useCallback, useEffect, useRef } from 'react';

// =============================================================================
// Common Validation Schemas
// =============================================================================

export const emailSchema = z
  .string()
  .min(1, 'Email is required')
  .email('Please enter a valid email address');

export const passwordSchema = z
  .string()
  .min(8, 'Password must be at least 8 characters')
  .regex(/[a-z]/, 'Password must contain at least one lowercase letter')
  .regex(/[A-Z]/, 'Password must contain at least one uppercase letter')
  .regex(/[0-9]/, 'Password must contain at least one number');

export const nameSchema = z
  .string()
  .min(1, 'Name is required')
  .max(100, 'Name must be less than 100 characters')
  .regex(/^[a-zA-Z\s'-]+$/, 'Name can only contain letters, spaces, hyphens, and apostrophes');

export const slugSchema = z
  .string()
  .min(1, 'Slug is required')
  .max(50, 'Slug must be less than 50 characters')
  .regex(/^[a-z0-9-]+$/, 'Slug can only contain lowercase letters, numbers, and hyphens')
  .refine((val) => !val.startsWith('-') && !val.endsWith('-'), {
    message: 'Slug cannot start or end with a hyphen',
  });

export const urlSchema = z
  .string()
  .url('Please enter a valid URL')
  .or(z.literal(''))
  .optional();

export const messageSchema = z
  .string()
  .min(10, 'Message must be at least 10 characters')
  .max(5000, 'Message must be less than 5000 characters');

// =============================================================================
// Validation Types
// =============================================================================

export interface FieldValidation {
  isValid: boolean;
  error: string | null;
  isValidating: boolean;
}

export interface FormValidation<T extends Record<string, unknown>> {
  values: T;
  errors: Partial<Record<keyof T, string>>;
  touched: Partial<Record<keyof T, boolean>>;
  isValid: boolean;
  isValidating: boolean;
  isDirty: boolean;
}

// =============================================================================
// useFieldValidation Hook
// =============================================================================

interface UseFieldValidationOptions<T> {
  schema: z.ZodType<T>;
  initialValue?: T;
  debounceMs?: number;
  validateOnChange?: boolean;
  validateOnBlur?: boolean;
}

/**
 * Hook for real-time field validation.
 *
 * @example
 * ```tsx
 * const email = useFieldValidation({
 *   schema: emailSchema,
 *   initialValue: '',
 * });
 *
 * <input
 *   value={email.value}
 *   onChange={(e) => email.setValue(e.target.value)}
 *   onBlur={email.handleBlur}
 * />
 * {email.touched && email.error && <span>{email.error}</span>}
 * ```
 */
export function useFieldValidation<T>({
  schema,
  initialValue,
  debounceMs = 300,
  validateOnChange = true,
  validateOnBlur = true,
}: UseFieldValidationOptions<T>) {
  const [value, setValue] = useState<T | undefined>(initialValue);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [isValidating, setIsValidating] = useState(false);

  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  const validate = useCallback(
    async (val: T | undefined): Promise<boolean> => {
      setIsValidating(true);
      try {
        await schema.parseAsync(val);
        setError(null);
        return true;
      } catch (err) {
        if (err instanceof z.ZodError) {
          setError(err.errors[0]?.message || 'Invalid value');
        } else {
          setError('Validation failed');
        }
        return false;
      } finally {
        setIsValidating(false);
      }
    },
    [schema]
  );

  const debouncedValidate = useCallback(
    (val: T | undefined) => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = setTimeout(() => {
        validate(val);
      }, debounceMs);
    },
    [validate, debounceMs]
  );

  const handleChange = useCallback(
    (val: T) => {
      setValue(val);
      if (validateOnChange && touched) {
        debouncedValidate(val);
      }
    },
    [validateOnChange, touched, debouncedValidate]
  );

  const handleBlur = useCallback(() => {
    setTouched(true);
    if (validateOnBlur) {
      validate(value);
    }
  }, [validateOnBlur, validate, value]);

  const reset = useCallback(() => {
    setValue(initialValue);
    setError(null);
    setTouched(false);
    setIsValidating(false);
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
  }, [initialValue]);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return {
    value,
    setValue: handleChange,
    error,
    touched,
    isValidating,
    isValid: error === null && !isValidating,
    isDirty: value !== initialValue,
    handleBlur,
    validate: () => validate(value),
    reset,
  };
}

// =============================================================================
// useFormValidation Hook
// =============================================================================

interface UseFormValidationOptions<T extends z.ZodRawShape> {
  schema: z.ZodObject<T>;
  initialValues: z.infer<z.ZodObject<T>>;
  onSubmit?: (values: z.infer<z.ZodObject<T>>) => void | Promise<void>;
  validateOnChange?: boolean;
  validateOnBlur?: boolean;
}

/**
 * Hook for form-level validation with real-time field validation.
 *
 * @example
 * ```tsx
 * const form = useFormValidation({
 *   schema: z.object({
 *     email: emailSchema,
 *     password: passwordSchema,
 *   }),
 *   initialValues: { email: '', password: '' },
 *   onSubmit: async (values) => {
 *     await submitForm(values);
 *   },
 * });
 *
 * <form onSubmit={form.handleSubmit}>
 *   <input
 *     {...form.getFieldProps('email')}
 *   />
 *   {form.getFieldError('email')}
 * </form>
 * ```
 */
export function useFormValidation<T extends z.ZodRawShape>({
  schema,
  initialValues,
  onSubmit,
  validateOnChange = true,
  validateOnBlur = true,
}: UseFormValidationOptions<T>) {
  type FormValues = z.infer<z.ZodObject<T>>;
  type FieldName = keyof FormValues;

  const [values, setValues] = useState<FormValues>(initialValues);
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({});
  const [touched, setTouched] = useState<Partial<Record<FieldName, boolean>>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isValidating, setIsValidating] = useState(false);

  const timeoutRefs = useRef<Map<FieldName, NodeJS.Timeout>>(new Map());

  const validateField = useCallback(
    async (name: FieldName, value: unknown): Promise<string | null> => {
      try {
        const fieldSchema = schema.shape[name as keyof T];
        if (fieldSchema) {
          await fieldSchema.parseAsync(value);
        }
        return null;
      } catch (err) {
        if (err instanceof z.ZodError) {
          return err.errors[0]?.message || 'Invalid value';
        }
        return 'Validation failed';
      }
    },
    [schema]
  );

  const validateForm = useCallback(async (): Promise<boolean> => {
    setIsValidating(true);
    try {
      await schema.parseAsync(values);
      setErrors({});
      return true;
    } catch (err) {
      if (err instanceof z.ZodError) {
        const newErrors: Partial<Record<FieldName, string>> = {};
        for (const issue of err.errors) {
          const path = issue.path[0] as FieldName;
          if (path && !newErrors[path]) {
            newErrors[path] = issue.message;
          }
        }
        setErrors(newErrors);
      }
      return false;
    } finally {
      setIsValidating(false);
    }
  }, [schema, values]);

  const setFieldValue = useCallback(
    (name: FieldName, value: unknown) => {
      setValues((prev) => ({ ...prev, [name]: value }));

      if (validateOnChange && touched[name]) {
        // Clear existing timeout
        const existingTimeout = timeoutRefs.current.get(name);
        if (existingTimeout) {
          clearTimeout(existingTimeout);
        }

        // Debounced validation
        const timeout = setTimeout(async () => {
          const error = await validateField(name, value);
          setErrors((prev) => {
            if (error) {
              return { ...prev, [name]: error };
            }
            const { [name]: _, ...rest } = prev;
            return rest as Partial<Record<FieldName, string>>;
          });
        }, 300);

        timeoutRefs.current.set(name, timeout);
      }
    },
    [validateOnChange, touched, validateField]
  );

  const setFieldTouched = useCallback(
    async (name: FieldName) => {
      setTouched((prev) => ({ ...prev, [name]: true }));

      if (validateOnBlur) {
        const error = await validateField(name, values[name]);
        setErrors((prev) => {
          if (error) {
            return { ...prev, [name]: error };
          }
          const { [name]: _, ...rest } = prev;
          return rest as Partial<Record<FieldName, string>>;
        });
      }
    },
    [validateOnBlur, validateField, values]
  );

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault();

      // Mark all fields as touched
      const allTouched = Object.keys(initialValues).reduce(
        (acc, key) => ({ ...acc, [key]: true }),
        {} as Record<FieldName, boolean>
      );
      setTouched(allTouched);

      // Validate entire form
      const isValid = await validateForm();
      if (!isValid) {
        return;
      }

      // Submit
      setIsSubmitting(true);
      try {
        await onSubmit?.(values);
      } finally {
        setIsSubmitting(false);
      }
    },
    [initialValues, validateForm, onSubmit, values]
  );

  const getFieldProps = useCallback(
    (name: FieldName) => ({
      value: values[name] as string,
      onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        setFieldValue(name, e.target.value),
      onBlur: () => setFieldTouched(name),
      'aria-invalid': touched[name] && !!errors[name],
      'aria-describedby': errors[name] ? `${String(name)}-error` : undefined,
    }),
    [values, setFieldValue, setFieldTouched, touched, errors]
  );

  const getFieldError = useCallback(
    (name: FieldName): string | undefined => {
      return touched[name] ? errors[name] : undefined;
    },
    [touched, errors]
  );

  const reset = useCallback(() => {
    setValues(initialValues);
    setErrors({});
    setTouched({});
    setIsSubmitting(false);
    setIsValidating(false);

    // Clear all timeouts
    timeoutRefs.current.forEach((timeout) => clearTimeout(timeout));
    timeoutRefs.current.clear();
  }, [initialValues]);

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      timeoutRefs.current.forEach((timeout) => clearTimeout(timeout));
    };
  }, []);

  const isValid = Object.keys(errors).length === 0;
  const isDirty = JSON.stringify(values) !== JSON.stringify(initialValues);

  return {
    values,
    errors,
    touched,
    isValid,
    isDirty,
    isSubmitting,
    isValidating,
    setFieldValue,
    setFieldTouched,
    handleSubmit,
    getFieldProps,
    getFieldError,
    validateForm,
    reset,
  };
}

// =============================================================================
// Validation Helpers
// =============================================================================

/**
 * Create a debounced async validator function.
 */
export function createDebouncedValidator<T>(
  validator: (value: T) => Promise<string | null>,
  debounceMs = 300
) {
  let timeoutId: NodeJS.Timeout | null = null;
  let lastPromise: Promise<string | null> | null = null;

  return (value: T): Promise<string | null> => {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }

    return new Promise((resolve) => {
      timeoutId = setTimeout(async () => {
        lastPromise = validator(value);
        const result = await lastPromise;
        resolve(result);
      }, debounceMs);
    });
  };
}

/**
 * Validate an email format without async operations.
 */
export function isValidEmail(email: string): boolean {
  const result = emailSchema.safeParse(email);
  return result.success;
}

/**
 * Get password strength score (0-4).
 */
export function getPasswordStrength(password: string): {
  score: number;
  feedback: string;
} {
  let score = 0;
  const feedback: string[] = [];

  if (password.length >= 8) score++;
  else feedback.push('Use at least 8 characters');

  if (/[a-z]/.test(password)) score++;
  else feedback.push('Add lowercase letters');

  if (/[A-Z]/.test(password)) score++;
  else feedback.push('Add uppercase letters');

  if (/[0-9]/.test(password)) score++;
  else feedback.push('Add numbers');

  if (/[^a-zA-Z0-9]/.test(password)) score++;
  else feedback.push('Add special characters');

  const strengthLabels = ['Very weak', 'Weak', 'Fair', 'Good', 'Strong'];

  return {
    score: Math.min(score, 4),
    feedback: feedback.length > 0 ? feedback[0] : strengthLabels[score] || 'Strong',
  };
}
