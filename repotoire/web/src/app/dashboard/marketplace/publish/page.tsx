'use client';

import { useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useDropzone } from 'react-dropzone';
import {
  Upload,
  FileArchive,
  X,
  Loader2,
  Check,
  AlertCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { usePublishAsset } from '@/lib/marketplace-hooks';
import { AssetType, PricingType, PublishRequest } from '@/types/marketplace';

const assetTypes: { value: AssetType; label: string; description: string }[] = [
  { value: 'command', label: 'Command', description: 'A slash command for Claude Code' },
  { value: 'skill', label: 'Skill', description: 'A capability or tool for AI assistants' },
  { value: 'style', label: 'Style', description: 'A coding style configuration' },
  { value: 'hook', label: 'Hook', description: 'A hook that runs on events' },
  { value: 'prompt', label: 'Prompt', description: 'A reusable prompt template' },
];

const pricingTypes: { value: PricingType; label: string; description: string }[] = [
  { value: 'free', label: 'Free', description: 'Available to everyone' },
  { value: 'freemium', label: 'Freemium', description: 'Free with premium features' },
  { value: 'paid', label: 'Paid', description: 'Requires purchase' },
];

export default function PublishPage() {
  const router = useRouter();
  const { trigger: publish, isMutating: isPublishing } = usePublishAsset();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const [formData, setFormData] = useState<PublishRequest>({
    name: '',
    slug: '',
    description: '',
    type: 'command',
    version: '1.0.0',
    tags: [],
    pricing_type: 'free',
    price_cents: 0,
  });

  const [tagInput, setTagInput] = useState('');

  const onDrop = useCallback((acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      const uploadedFile = acceptedFiles[0];
      // Validate file type
      if (!uploadedFile.name.endsWith('.tar.gz') && !uploadedFile.name.endsWith('.tgz')) {
        setError('Please upload a .tar.gz or .tgz file');
        return;
      }
      setFile(uploadedFile);
      setError(null);
    }
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'application/gzip': ['.tar.gz', '.tgz'],
    },
    maxFiles: 1,
  });

  const handleInputChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: name === 'price_cents' ? parseInt(value) * 100 : value,
    }));

    // Auto-generate slug from name
    if (name === 'name') {
      const slug = value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');
      setFormData((prev) => ({ ...prev, slug }));
    }
  };

  const handleAddTag = () => {
    const tag = tagInput.trim().toLowerCase();
    if (tag && !formData.tags.includes(tag)) {
      setFormData((prev) => ({
        ...prev,
        tags: [...prev.tags, tag],
      }));
      setTagInput('');
    }
  };

  const handleRemoveTag = (tagToRemove: string) => {
    setFormData((prev) => ({
      ...prev,
      tags: prev.tags.filter((t) => t !== tagToRemove),
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!file) {
      setError('Please upload an asset file');
      return;
    }

    if (!formData.name || !formData.slug || !formData.description) {
      setError('Please fill in all required fields');
      return;
    }

    try {
      await publish({ data: formData, file });
      setSuccess(true);
      // Redirect after success
      setTimeout(() => {
        router.push('/dashboard/marketplace');
      }, 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to publish asset');
    }
  };

  if (success) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Card className="max-w-md w-full">
          <CardContent className="pt-6 text-center">
            <div className="w-12 h-12 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-4">
              <Check className="w-6 h-6 text-emerald-500" />
            </div>
            <h2 className="text-xl font-bold text-foreground mb-2">
              Asset Published!
            </h2>
            <p className="text-muted-foreground mb-4">
              Your asset "{formData.name}" has been published to the marketplace.
            </p>
            <p className="text-sm text-muted-foreground">
              Redirecting to your assets...
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Publish Asset</h1>
        <p className="text-muted-foreground">
          Share your commands, skills, and more with the community
        </p>
      </div>

      {/* Error Message */}
      {error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-destructive shrink-0" />
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* File Upload */}
        <Card>
          <CardHeader>
            <CardTitle>Asset File</CardTitle>
            <CardDescription>
              Upload your asset as a gzipped tarball (.tar.gz)
            </CardDescription>
          </CardHeader>
          <CardContent>
            {file ? (
              <div className="flex items-center justify-between p-4 rounded-lg border border-border bg-muted/50">
                <div className="flex items-center gap-3">
                  <FileArchive className="w-8 h-8 text-muted-foreground" />
                  <div>
                    <p className="font-medium text-foreground">{file.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {(file.size / 1024).toFixed(2)} KB
                    </p>
                  </div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  onClick={() => setFile(null)}
                >
                  <X className="w-4 h-4" />
                </Button>
              </div>
            ) : (
              <div
                {...getRootProps()}
                className={cn(
                  'border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors',
                  isDragActive
                    ? 'border-primary bg-primary/5'
                    : 'border-border hover:border-primary/50'
                )}
              >
                <input {...getInputProps()} />
                <Upload className="w-10 h-10 mx-auto text-muted-foreground mb-4" />
                <p className="text-foreground font-medium mb-1">
                  {isDragActive
                    ? 'Drop your file here'
                    : 'Drag & drop your asset file'}
                </p>
                <p className="text-sm text-muted-foreground">
                  or click to browse (.tar.gz or .tgz)
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Basic Info */}
        <Card>
          <CardHeader>
            <CardTitle>Basic Information</CardTitle>
            <CardDescription>
              Tell users about your asset
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name *</Label>
              <Input
                id="name"
                name="name"
                placeholder="My Awesome Command"
                value={formData.name}
                onChange={handleInputChange}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="slug">Slug *</Label>
              <Input
                id="slug"
                name="slug"
                placeholder="my-awesome-command"
                value={formData.slug}
                onChange={handleInputChange}
                required
              />
              <p className="text-xs text-muted-foreground">
                This will be used in the asset URL and install command
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="description">Description *</Label>
              <Textarea
                id="description"
                name="description"
                placeholder="A brief description of what your asset does..."
                value={formData.description}
                onChange={handleInputChange}
                rows={3}
                required
              />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="type">Type *</Label>
                <Select
                  value={formData.type}
                  onValueChange={(value) =>
                    setFormData((prev) => ({ ...prev, type: value as AssetType }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select type" />
                  </SelectTrigger>
                  <SelectContent>
                    {assetTypes.map((type) => (
                      <SelectItem key={type.value} value={type.value}>
                        {type.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="version">Version *</Label>
                <Input
                  id="version"
                  name="version"
                  placeholder="1.0.0"
                  value={formData.version}
                  onChange={handleInputChange}
                  required
                />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Pricing */}
        <Card>
          <CardHeader>
            <CardTitle>Pricing</CardTitle>
            <CardDescription>
              Choose how you want to monetize your asset
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Pricing Model</Label>
              <div className="grid gap-4 md:grid-cols-3">
                {pricingTypes.map((pricing) => (
                  <button
                    key={pricing.value}
                    type="button"
                    onClick={() =>
                      setFormData((prev) => ({
                        ...prev,
                        pricing_type: pricing.value,
                        price_cents: pricing.value === 'free' ? 0 : prev.price_cents,
                      }))
                    }
                    className={cn(
                      'flex flex-col items-start gap-1 rounded-lg border p-4 text-left transition-colors',
                      formData.pricing_type === pricing.value
                        ? 'border-primary bg-primary/5'
                        : 'border-border hover:border-primary/50'
                    )}
                  >
                    <span className="font-medium">{pricing.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {pricing.description}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {formData.pricing_type === 'paid' && (
              <div className="space-y-2">
                <Label htmlFor="price">Price (USD)</Label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
                    $
                  </span>
                  <Input
                    id="price"
                    name="price_cents"
                    type="number"
                    min="1"
                    step="1"
                    placeholder="9.99"
                    className="pl-7"
                    value={(formData.price_cents ?? 0) / 100 || ''}
                    onChange={handleInputChange}
                  />
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Tags */}
        <Card>
          <CardHeader>
            <CardTitle>Tags</CardTitle>
            <CardDescription>
              Help users discover your asset
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex gap-2">
              <Input
                placeholder="Add a tag..."
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleAddTag();
                  }
                }}
              />
              <Button type="button" variant="outline" onClick={handleAddTag}>
                Add
              </Button>
            </div>

            {formData.tags.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {formData.tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-1 px-3 py-1 text-sm bg-muted rounded-full"
                  >
                    {tag}
                    <button
                      type="button"
                      onClick={() => handleRemoveTag(tag)}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Optional Fields */}
        <Card>
          <CardHeader>
            <CardTitle>Additional Information</CardTitle>
            <CardDescription>Optional but recommended</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="readme">README (Markdown)</Label>
              <Textarea
                id="readme"
                name="readme"
                placeholder="# My Asset&#10;&#10;Detailed documentation about your asset..."
                value={formData.readme || ''}
                onChange={handleInputChange}
                rows={6}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="changelog">Changelog</Label>
              <Textarea
                id="changelog"
                name="changelog"
                placeholder="What's new in this version..."
                value={formData.changelog || ''}
                onChange={handleInputChange}
                rows={3}
              />
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="repository_url">Repository URL</Label>
                <Input
                  id="repository_url"
                  name="repository_url"
                  type="url"
                  placeholder="https://github.com/..."
                  value={formData.repository_url || ''}
                  onChange={handleInputChange}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="documentation_url">Documentation URL</Label>
                <Input
                  id="documentation_url"
                  name="documentation_url"
                  type="url"
                  placeholder="https://docs.example.com/..."
                  value={formData.documentation_url || ''}
                  onChange={handleInputChange}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="license">License</Label>
              <Input
                id="license"
                name="license"
                placeholder="MIT"
                value={formData.license || ''}
                onChange={handleInputChange}
              />
            </div>
          </CardContent>
        </Card>

        {/* Submit */}
        <div className="flex justify-end gap-4">
          <Button type="button" variant="outline" onClick={() => router.back()}>
            Cancel
          </Button>
          <Button type="submit" disabled={isPublishing}>
            {isPublishing ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Publishing...
              </>
            ) : (
              <>
                <Upload className="w-4 h-4 mr-2" />
                Publish Asset
              </>
            )}
          </Button>
        </div>
      </form>
    </div>
  );
}
